#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macOS 原生文件/文件夹拖放支持（纯标准库，ctypes + Objective-C 运行时）。
不依赖 tkinterdnd2 / pyobjc，可直接打包进自包含 app。

v2（重要稳定性修复）：
  - 为每一种 objc_msgSend 签名创建【独立】的函数指针，绝不修改全局共享的
    objc_msgSend.restype/argtypes。旧版在 AppKit 回调栈内反复改全局签名并重入，
    会造成 native 崩溃(SIGSEGV)——Python 层钩子完全抓不到。
  - 拖放类型字符串用 alloc/initWithUTF8String: 持有，不再缓存 autoreleased 对象
    （pool drain 后变野指针）。
  - performDragOperation: 返回类型用正确的 BOOL(1字节 signed char)，编码 c@:@。
  - 诊断日志写到 /tmp/remux_drop.log，native 崩溃前也能看到进行到哪一步。

用法：
    from drop_target import enable_drop
    enable_drop(root, entry_widget, on_drop=lambda path: ..., on_click=lambda: ...)
"""
import ctypes
import sys
import os
import tempfile
import datetime

P = ctypes.c_void_p
IDX = ctypes.c_size_t          # NSUInteger / NSDragOperation (64-bit)
BOOL = ctypes.c_int8           # macOS 64-bit BOOL 实为 signed char（1 字节）

_DROP_LOG = os.path.join(tempfile.gettempdir(), "remux_drop.log")


def _dlog(msg):
    try:
        with open(_DROP_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (
                datetime.datetime.now().isoformat(timespec="milliseconds"), msg))
    except Exception:
        pass


_is_mac = sys.platform == "darwin"
_objc = None
_MSGSEND_ADDR = None
_msg_cache = {}                # (restype, argtypes) -> 独立 CFUNCTYPE 函数指针
_filename_type_obj = None      # alloc/init 持有，永不释放（进程级）

_callbacks = {}     # token -> {"drop": fn, "click": fn}
_views = {}         # token -> view id（防止 GC）
_view_meta = {}     # view id(int) -> token
_keep_alive = []    # IMP 函数指针 + 持有的 OC 对象，防止 GC
_class_ready = False
_drop_class = None


def _load():
    global _objc, _MSGSEND_ADDR
    if _objc is not None:
        return _objc
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation",
                mode=ctypes.RTLD_GLOBAL)
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit",
                mode=ctypes.RTLD_GLOBAL)
    objc.objc_getClass.argtypes = [ctypes.c_char_p]; objc.objc_getClass.restype = P
    objc.sel_registerName.argtypes = [ctypes.c_char_p]; objc.sel_registerName.restype = P
    objc.objc_allocateClassPair.argtypes = [P, ctypes.c_char_p, IDX]; objc.objc_allocateClassPair.restype = P
    objc.objc_registerClassPair.argtypes = [P]
    objc.class_addMethod.argtypes = [P, P, P, ctypes.c_char_p]; objc.class_addMethod.restype = ctypes.c_int
    objc.class_addProtocol.argtypes = [P, P]; objc.class_addProtocol.restype = ctypes.c_int
    # 取 objc_msgSend 的原始地址；后续按签名构造独立函数指针，不碰这个全局对象
    objc.objc_msgSend.restype = P
    objc.objc_msgSend.argtypes = [P, P]
    _MSGSEND_ADDR = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    _objc = objc
    return objc


def _msg(restype, *argtypes):
    """按 (返回类型, 参数类型...) 返回【独立】的 objc_msgSend 函数指针。
    每个签名各自持有 restype/argtypes，互不干扰，避免重入时的竞态与栈破坏。"""
    key = (restype, argtypes)
    fn = _msg_cache.get(key)
    if fn is None:
        proto = ctypes.CFUNCTYPE(restype, P, P, *argtypes)
        fn = proto(_MSGSEND_ADDR)
        _msg_cache[key] = fn
    return fn


def _cls(name):
    return _load().objc_getClass(name.encode())


def _seln(name):
    return _load().sel_registerName(name.encode())


def _send(obj, sel, *args):
    """通用发送：所有参数按指针处理，返回指针。用于对象型参数/返回值。"""
    return _msg(P, *([P] * len(args)))(obj, sel, *args)


def _send_count(obj, sel):
    """无参消息，返回 NSUInteger（如 NSArray count）。"""
    return _msg(IDX)(obj, sel)


def _send_cstr(obj, sel):
    """无参消息，返回 const char*（如 NSString UTF8String）。"""
    return _msg(ctypes.c_char_p)(obj, sel)


def _filename_type():
    """拖放类型字符串。用 alloc/init 持有，避免 autoreleased 对象被回收后成为野指针。"""
    global _filename_type_obj
    if _filename_type_obj is None:
        _dlog("创建并持有 NSFilenamesPboardType 字符串")
        ns = _msg(P)(_cls("NSString"), _seln("alloc"))
        # alloc 返回对象；再发 initWithUTF8String:
        ns = _msg(P, ctypes.c_char_p)(
            ns, _seln("initWithUTF8String:"), b"NSFilenamesPboardType")
        _filename_type_obj = ns
        _keep_alive.append(ns)  # 进程级持有
    return _filename_type_obj


def _cb_for(view_ptr):
    token = _view_meta.get(int(view_ptr))
    return _callbacks.get(token) if token else None


# ---------- IMP 回调 ----------
IMP_DRAG = ctypes.CFUNCTYPE(IDX, P, P, P)     # draggingEntered/Over/prepare -> NSDragOperation
IMP_BOOL = ctypes.CFUNCTYPE(BOOL, P, P, P)    # performDragOperation -> BOOL(1字节)
IMP_VOID = ctypes.CFUNCTYPE(None, P, P, P)    # mouseDown -> void


def _imp_entered(self_, cmd, sender):
    return 1  # NSDragOperationCopy


def _imp_over(self_, cmd, sender):
    return 1


def _imp_prepare(self_, cmd, sender):
    return 1


def _imp_perform(self_, cmd, sender):
    try:
        _dlog("performDragOperation 回调进入")
        cbs = _cb_for(self_)
        if not cbs or not cbs.get("drop"):
            _dlog("无 drop 回调，返回 NO")
            return 0
        pboard = _send(sender, _seln("draggingPasteboard"))
        if not pboard:
            _dlog("draggingPasteboard 为空")
            return 0
        # propertyListForType: -> NSArray（参数 NSString*，返回对象）
        arr = _msg(P, P)(pboard, _seln("propertyListForType:"), _filename_type())
        if not arr:
            _dlog("propertyListForType 为空")
            return 0
        cnt = _send_count(arr, _seln("count"))
        _dlog("拖入项目数: %d" % cnt)
        if cnt < 1:
            return 0
        # objectAtIndex: 参数是 NSUInteger（IDX），返回对象
        ns_path = _msg(P, IDX)(arr, _seln("objectAtIndex:"), 0)
        if not ns_path:
            _dlog("objectAtIndex:0 为空")
            return 0
        cpath = _send_cstr(ns_path, _seln("UTF8String"))
        if not cpath:
            _dlog("UTF8String 为空")
            return 0
        path = cpath.decode("utf-8", "replace")
        _dlog("拖入路径: %s" % path)
        cbs["drop"](path)
        _dlog("drop 回调完成")
        return 1
    except Exception as e:
        _dlog("perform 异常: %r" % (e,))
        return 0


def _imp_mousedown(self_, cmd, event):
    try:
        cbs = _cb_for(self_)
        if cbs and cbs.get("click"):
            cbs["click"]()
    except Exception:
        pass


def _build_class():
    global _class_ready, _drop_class
    if _class_ready:
        return _drop_class
    objc = _load()
    name = b"RemuxDropView2"
    newcls = objc.objc_allocateClassPair(_cls("NSView"), name, 0)
    if not newcls:
        _drop_class = objc.objc_getClass(name)
        _class_ready = True
        return _drop_class

    # NSDragOperation 返回：编码 Q@:@（NSUInteger=64位 unsigned long long）
    for sel_name, fn in [
        ("draggingEntered:", _imp_entered),
        ("draggingOver:", _imp_over),
        ("prepareForDragOperation:", _imp_prepare),
    ]:
        imp = IMP_DRAG(fn)
        objc.class_addMethod(newcls, _seln(sel_name), ctypes.cast(imp, P), b"Q@:@")
        _keep_alive.append(imp)

    # performDragOperation: 返回 BOOL(1字节)，编码 c@:@
    imp_perform = IMP_BOOL(_imp_perform)
    objc.class_addMethod(newcls, _seln("performDragOperation:"),
                         ctypes.cast(imp_perform, P), b"c@:@")
    _keep_alive.append(imp_perform)

    # mouseDown: 返回 void
    imp_click = IMP_VOID(_imp_mousedown)
    objc.class_addMethod(newcls, _seln("mouseDown:"),
                         ctypes.cast(imp_click, P), b"v@:@")
    _keep_alive.append(imp_click)

    try:
        proto = objc.protocol_getProtocol(b"NSDraggingDestination")
        if proto:
            objc.class_addProtocol(newcls, proto)
    except Exception:
        pass

    objc.objc_registerClassPair(newcls)
    _drop_class = newcls
    _class_ready = True
    return newcls


def enable_drop(root, widget, on_drop, on_click=None):
    """让控件支持拖放文件夹/文件。on_drop(path) 与 on_click() 均为 Python 回调。"""
    if not _is_mac:
        return False
    try:
        _dlog("enable_drop 开始")
        root.update_idletasks()
        cls = _build_class()

        nsapp = _send(_cls("NSApplication"), _seln("sharedApplication"))
        window = _send(nsapp, _seln("keyWindow")) or _send(nsapp, _seln("mainWindow"))
        if not window:
            wins = _send(nsapp, _seln("windows"))
            if wins and _send_count(wins, _seln("count")) > 0:
                window = _msg(P, IDX)(wins, _seln("objectAtIndex:"), 0)
        if not window:
            _dlog("未找到 NSWindow")
            return False
        content = _send(window, _seln("contentView"))
        if not content:
            _dlog("未找到 contentView")
            return False

        # 相对 root 窗口的坐标（winfo_x/y 是相对父控件，必须用 root 坐标换算）
        rx = widget.winfo_rootx() - root.winfo_rootx()
        ry = widget.winfo_rooty() - root.winfo_rooty()
        w, h = widget.winfo_width(), widget.winfo_height()
        win_h = root.winfo_height()
        mac_y = win_h - ry - h

        NSRect = type("NSRect", (ctypes.Structure,), {
            "_fields_": [("x", ctypes.c_double), ("y", ctypes.c_double),
                         ("w", ctypes.c_double), ("h", ctypes.c_double)]
        })
        frame = NSRect(rx, mac_y, w, h)
        # initWithFrame: 参数是 NSRect 结构体（按值传），返回对象
        inst = _msg(P, NSRect)(
            _send(cls, _seln("alloc")), _seln("initWithFrame:"), frame)

        arr = _msg(P, P)(_cls("NSArray"), _seln("arrayWithObject:"), _filename_type())
        _msg(None, P)(inst, _seln("registerForDraggedTypes:"), arr)
        _msg(None, P)(content, _seln("addSubview:"), inst)

        token = str(id(on_drop))
        _callbacks[token] = {"drop": on_drop, "click": on_click}
        _views[token] = inst
        _view_meta[int(inst)] = token
        _dlog("enable_drop 成功, token=%s" % token)
        return True
    except Exception as e:
        _dlog("enable_drop 失败: %r" % (e,))
        return False
