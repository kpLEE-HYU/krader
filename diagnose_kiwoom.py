"""Diagnostic script for Kiwoom OpenAPI+ connection issues."""

import sys
import time


def check_python_arch():
    """Check Python architecture."""
    import struct
    bits = struct.calcsize('P') * 8
    print(f"[1] Python Architecture: {bits}-bit")
    if bits != 32:
        print("    ERROR: Kiwoom OpenAPI+ requires 32-bit Python!")
        return False
    print("    OK")
    return True


def check_com_registration():
    """Check if Kiwoom OCX is registered."""
    print("\n[2] Checking COM Registration...")
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            r"KHOPENAPI.KHOpenAPICtrl.1\CLSID"
        )
        clsid, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        print(f"    CLSID: {clsid}")
        print("    OK - OCX is registered")
        return True
    except FileNotFoundError:
        print("    ERROR: Kiwoom OCX not registered!")
        print("    Solution: Reinstall Kiwoom OpenAPI+ or run 'regsvr32 KHOPENAPI.ocx'")
        return False
    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def check_pywin32():
    """Check pywin32 installation."""
    print("\n[3] Checking pywin32...")
    try:
        import pythoncom
        import win32com.client
        print(f"    pythoncom: OK")
        print(f"    win32com: OK")
        return True
    except ImportError as e:
        print(f"    ERROR: {e}")
        print("    Solution: pip install pywin32")
        return False


def check_com_creation():
    """Try to create the COM object without events."""
    print("\n[4] Testing COM Object Creation (without events)...")
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        ocx = win32com.client.Dispatch("KHOPENAPI.KHOpenAPICtrl.1")
        print("    OK - COM object created successfully")

        # Try to get connection status
        status = ocx.GetConnectState()
        print(f"    Connection state: {status} (0=disconnected, 1=connected)")

        return True, ocx
    except Exception as e:
        print(f"    ERROR: {e}")
        return False, None


def check_com_with_events():
    """Try to create COM object with events."""
    print("\n[5] Testing COM Object with Events...")
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()

        class TestEvents:
            def OnEventConnect(self, err_code):
                print(f"    Event received: OnEventConnect({err_code})")

        ocx = win32com.client.DispatchWithEvents(
            "KHOPENAPI.KHOpenAPICtrl.1", TestEvents
        )
        print("    OK - COM object with events created")
        return True, ocx
    except Exception as e:
        print(f"    ERROR: {e}")
        error_code = getattr(e, 'args', [None])[0]
        if error_code == -2147418113:
            print("\n    This error often means:")
            print("    - Missing Windows message pump (need GUI or message loop)")
            print("    - Another OpenAPI instance is already connected")
            print("    - HTS is not running or not logged in")
        return False, None


def check_with_message_pump():
    """Try with a simple message pump."""
    print("\n[6] Testing with Message Pump...")
    try:
        import pythoncom
        import win32com.client
        import threading

        result = {"success": False, "error": None, "ocx": None}

        def com_thread():
            try:
                pythoncom.CoInitialize()

                class TestEvents:
                    def OnEventConnect(self, err_code):
                        print(f"    Event: OnEventConnect({err_code})")
                        result["connected"] = (err_code == 0)

                ocx = win32com.client.DispatchWithEvents(
                    "KHOPENAPI.KHOpenAPICtrl.1", TestEvents
                )
                result["ocx"] = ocx
                result["success"] = True
                print("    OK - COM created in dedicated thread")

                # Run message pump for a short time
                print("    Running message pump for 2 seconds...")
                end_time = time.time() + 2
                while time.time() < end_time:
                    pythoncom.PumpWaitingMessages()
                    time.sleep(0.1)

            except Exception as e:
                result["error"] = e
                print(f"    ERROR in thread: {e}")
            finally:
                pythoncom.CoUninitialize()

        thread = threading.Thread(target=com_thread)
        thread.start()
        thread.join(timeout=5)

        if result["success"]:
            print("    Message pump test completed")
        return result["success"]

    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def main():
    print("=" * 60)
    print("Kiwoom OpenAPI+ Diagnostic Tool")
    print("=" * 60)

    if sys.platform != "win32":
        print("ERROR: This script must be run on Windows!")
        return

    # Run checks
    check_python_arch()
    check_pywin32()
    check_com_registration()

    success_basic, ocx = check_com_creation()

    if success_basic:
        success_events, _ = check_com_with_events()

        if not success_events:
            print("\n" + "=" * 60)
            print("Event binding failed. Trying with message pump...")
            check_with_message_pump()

    print("\n" + "=" * 60)
    print("CHECKLIST:")
    print("=" * 60)
    print("[ ] Is Kiwoom HTS running?")
    print("[ ] Is Kiwoom HTS logged in?")
    print("[ ] Is 'Open API' enabled in HTS settings?")
    print("[ ] Are there any other programs using OpenAPI?")
    print("[ ] Close all Python processes and try again")
    print("")
    print("To enable Open API in HTS:")
    print("  HTS > 도구 > Open API 사용 설정")
    print("=" * 60)


if __name__ == "__main__":
    main()
