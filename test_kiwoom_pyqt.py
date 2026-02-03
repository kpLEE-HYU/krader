"""Test Kiwoom OpenAPI+ with PyQt5 QAxWidget."""

import sys


def main():
    print("=" * 60)
    print("Kiwoom OpenAPI+ PyQt5 Test")
    print("=" * 60)

    # Step 1: Check PyQt5
    print("\n[1] Checking PyQt5...")
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QAxContainer import QAxWidget
        print("    PyQt5 OK")
    except ImportError as e:
        print(f"    ERROR: {e}")
        print("    Solution: pip install PyQt5")
        return 1

    # Step 2: Create QApplication
    print("\n[2] Creating QApplication...")
    app = QApplication(sys.argv)
    print("    QApplication created")

    # Step 3: Create QAxWidget with Kiwoom OCX
    print("\n[3] Creating Kiwoom QAxWidget...")
    try:
        ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        print("    QAxWidget created successfully")
    except Exception as e:
        print(f"    ERROR: {e}")
        return 1

    # Step 4: Test GetConnectState
    print("\n[4] Testing GetConnectState()...")
    try:
        state = ocx.dynamicCall("GetConnectState()")
        print(f"    Connection state: {state} (0=disconnected, 1=connected)")
        print("    SUCCESS - Method call works!")
    except Exception as e:
        print(f"    ERROR: {e}")
        return 1

    # Step 5: Test login (this will show a popup)
    print("\n[5] Testing CommConnect (Login)...")
    print("    This will open a login popup window.")

    login_result = {"code": None}

    def on_event_connect(err_code):
        """Handle login result."""
        login_result["code"] = err_code
        if err_code == 0:
            print(f"    Login SUCCESS (code: {err_code})")
            # Get account info
            accounts = ocx.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            print(f"    Accounts: {accounts}")
        else:
            print(f"    Login FAILED (code: {err_code})")
        app.quit()

    # Connect the event
    ocx.OnEventConnect.connect(on_event_connect)

    # Request login
    result = ocx.dynamicCall("CommConnect()")
    print(f"    CommConnect() returned: {result}")

    if result == 0:
        print("    Waiting for login... (a popup window should appear)")
        print("    Please log in with your Kiwoom credentials.")
        app.exec_()  # Run event loop until login completes
    else:
        print(f"    CommConnect failed with code: {result}")
        return 1

    print("\n" + "=" * 60)
    if login_result["code"] == 0:
        print("TEST PASSED - Kiwoom OpenAPI+ is working correctly!")
    else:
        print(f"TEST FAILED - Login error code: {login_result['code']}")
    print("=" * 60)

    return 0 if login_result["code"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
