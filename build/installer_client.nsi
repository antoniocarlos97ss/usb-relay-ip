Unicode true

!define APP_NAME "USBRelayClient"
!define APP_VERSION "1.0.0"
!define COMPANY_NAME "OhMyTech"
!define PRODUCT_NAME "USB Relay IP"
!define INSTALL_DIR "$LOCALAPPDATA\\Programs\\USBRelayClient"
!define START_MENU_DIR "USB Relay IP"

Name "${PRODUCT_NAME} - Client"
OutFile "..\\dist\\USBRelayClient_Setup.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKCU "Software\\${COMPANY_NAME}\\${APP_NAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "..\\client\\assets\\icon.ico"
!define MUI_UNICON "..\\client\\assets\\icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\\README.md"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "PortugueseBR"
!insertmacro MUI_LANGUAGE "English"

Section "USB Relay IP Client" SEC01
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    File /r "..\\dist\\USBRelayClient\\*"

    WriteUninstaller "$INSTDIR\\Uninstall.exe"
    WriteRegStr HKCU "Software\\${COMPANY_NAME}\\${APP_NAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "DisplayName" "${PRODUCT_NAME} - Client"
    WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "UninstallString" '"$INSTDIR\\Uninstall.exe"'
    WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "Publisher" "${COMPANY_NAME}"
    WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "InstallLocation" "$INSTDIR"

    CreateDirectory "$SMPROGRAMS\\${START_MENU_DIR}"
    CreateShortCut "$SMPROGRAMS\\${START_MENU_DIR}\\USB Relay IP Client.lnk" "$INSTDIR\\USBRelayClient.exe"
    CreateShortCut "$DESKTOP\\USB Relay IP Client.lnk" "$INSTDIR\\USBRelayClient.exe"
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    Delete "$DESKTOP\\USB Relay IP Client.lnk"
    Delete "$SMPROGRAMS\\${START_MENU_DIR}\\USB Relay IP Client.lnk"
    RMDir "$SMPROGRAMS\\${START_MENU_DIR}"
    DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}"
    DeleteRegKey HKCU "Software\\${COMPANY_NAME}\\${APP_NAME}"
    RMDir /r "$INSTDIR"
SectionEnd
