# Port status

wine-uwp `ac56cede08bb` (Wine 11.20) onto `GE-Proton11-9`.

| | files |
| --- | --- |
| merged cleanly | 198 |
| new in wine-uwp, copied | 216 |
| added upstream after 11.0, copied whole | 9 |
| generated, regenerated after merge | 6 |
| conflicted in the automatic merge | 56 |
| still conflicted after resolutions/ | 56 (90 hunks) |

Resolution patches applied: none

## Still to resolve

| file | hunks |
| --- | --- |
| `dlls/kernelbase/version.c` | 13 |
| `dlls/ninput/main.c` | 6 |
| `dlls/ntdll/unix/virtual.c` | 4 |
| `dlls/iertutil/main.c` | 3 |
| `dlls/windows.web/json_value.c` | 3 |
| `configure.ac` | 2 |
| `dlls/combase/combase_private.h` | 2 |
| `dlls/kernelbase/kernelbase.spec` | 2 |
| `dlls/ninput/ninput.spec` | 2 |
| `dlls/ntdll/ntdll.spec` | 2 |
| `dlls/win32u/input.c` | 2 |
| `dlls/windows.web/json_object.c` | 2 |
| `include/Makefile.in` | 2 |
| `include/winternl.h` | 2 |
| `dlls/winhttp/winhttp_private.h` | 2 |
| `dlls/combase/combase.spec` | 1 |
| `dlls/combase/roapi.c` | 1 |
| `dlls/d2d1/Makefile.in` | 1 |
| `dlls/dcomp/device.c` | 1 |
| `dlls/dcomp/Makefile.in` | 1 |
| `dlls/dwmapi/Makefile.in` | 1 |
| `dlls/dwrite/font.c` | 1 |
| `dlls/dwrite/opentype.c` | 1 |
| `dlls/dxgi/dxgi_private.h` | 1 |
| `dlls/iertutil/iertutil.spec` | 1 |
| `dlls/iertutil/Makefile.in` | 1 |
| `dlls/iertutil/private.h` | 1 |
| `dlls/msvcrt/msvcrt.spec` | 1 |
| `dlls/ntdll/unix/signal_x86_64.c` | 1 |
| `dlls/sechost/service.c` | 1 |
| `dlls/shcore/main.c` | 1 |
| `dlls/shcore/shcore.spec` | 1 |
| `dlls/ucrtbase/ucrtbase.spec` | 1 |
| `dlls/user32/input.c` | 1 |
| `dlls/user32/user32.spec` | 1 |
| `dlls/uxtheme/system.c` | 1 |
| `dlls/win32u/font.c` | 1 |
| `dlls/win32u/message.c` | 1 |
| `dlls/win32u/ntuser_private.h` | 1 |
| `dlls/win32u/window.c` | 1 |
| `dlls/windows.ui.core.textinput/classes.idl` | 1 |
| `dlls/windows.ui.core.textinput/main.c` | 1 |
| `dlls/windows.ui.core.textinput/Makefile.in` | 1 |
| `dlls/windows.ui/inputpane.c` | 1 |
| `dlls/windows.web/private.h` | 1 |
| `dlls/wined3d/adapter_vk.c` | 1 |
| `dlls/winex11.drv/bitblt.c` | 1 |
| `dlls/winex11.drv/window.c` | 1 |
| `dlls/wow64/syscall.c` | 1 |
| `dlls/wow64win/user.c` | 1 |
| `.gitignore` | 1 |
| `include/appmodel.h` | 1 |
| `include/rpcasync.h` | 1 |
| `dlls/winhttp/request.c` | 1 |
| `libs/vkd3d/libs/vkd3d/command.c` | 1 |
| `libs/vkd3d/libs/vkd3d/vkd3d_private.h` | 1 |

## Files added upstream after 11.0

Copied whole from wine-uwp; check they do not need other post-11.0 changes.

- `dlls/ntdll/unix/alpc.c`
- `dlls/windows.graphics/main.c`
- `dlls/windows.graphics/Makefile.in`
- `dlls/windows.graphics/private.h`
- `dlls/windows.ui.core.textinput/services.c`
- `dlls/windows.web/json_array.c`
- `include/windows.ui.text.core.idl`
- `server/alpc.c`
- `dlls/winhttp/socket.c`
