from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class RecycleBackend(Protocol):
    """Backend contract: success means the Shell accepted recycle semantics."""

    def recycle(self, path: Path) -> None: ...


class WindowsIFileOperationBackend:
    """Recycle through IFileOperation; never falls back to permanent deletion."""

    def recycle(self, path: Path) -> None:
        if os.name != "nt":
            raise OSError("Windows IFileOperation 后端仅支持 Windows")
        _recycle_with_ifileoperation(path)


def _recycle_with_ifileoperation(path: Path) -> None:
    import ctypes
    import uuid
    from ctypes import wintypes

    HRESULT = ctypes.c_long
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOFX_RECYCLEONDELETE = 0x00080000
    FOFX_EARLYFAILURE = 0x00100000
    CLSCTX_INPROC_SERVER = 0x1

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def parse(cls, value: str) -> "GUID":
            raw = uuid.UUID(value).bytes_le
            return cls.from_buffer_copy(raw)

    def failed(result: int) -> bool:
        return result < 0

    def method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(vtable[index])

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    initialized = ole32.CoInitializeEx(None, 0x2)
    should_uninitialize = initialized in (0, 1)
    operation = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        clsid = GUID.parse("3ad05575-8857-4850-9277-11b85bdb8e09")
        iid_operation = GUID.parse("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(iid_operation), ctypes.byref(operation),
        )
        if failed(result):
            raise OSError(f"无法创建 IFileOperation: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        iid_item = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
        result = shell32.SHCreateItemFromParsingName(
            str(path), None, ctypes.byref(iid_item), ctypes.byref(item)
        )
        if failed(result):
            raise OSError(f"无法创建 Shell 项目: HRESULT 0x{result & 0xFFFFFFFF:08X}")

        flags = FOF_NOCONFIRMATION | FOF_NOERRORUI | FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE
        result = method(operation, 5, HRESULT, wintypes.DWORD)(operation, flags)
        if failed(result):
            raise OSError(f"无法设置回收站语义: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        result = method(operation, 18, HRESULT, ctypes.c_void_p, ctypes.c_void_p)(operation, item, None)
        if failed(result):
            raise OSError(f"无法排入回收操作: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        result = method(operation, 21, HRESULT)(operation)
        if failed(result):
            raise OSError(f"回收站操作失败: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        aborted = wintypes.BOOL()
        result = method(operation, 22, HRESULT, ctypes.POINTER(wintypes.BOOL))(operation, ctypes.byref(aborted))
        if failed(result) or aborted.value:
            raise OSError("回收站操作被取消或状态无法确认")
    finally:
        if item:
            method(item, 2, wintypes.ULONG)(item)
        if operation:
            method(operation, 2, wintypes.ULONG)(operation)
        if should_uninitialize:
            ole32.CoUninitialize()
