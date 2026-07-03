Unicode true

!include "LogicLib.nsh"

!define APP_NAME "USBRelayHost"
!define APP_VERSION "1.0.0"
!define COMPANY_NAME "OhMyTech"
!define PRODUCT_NAME "USB Relay IP"
!define INSTALL_DIR "$PROGRAMFILES64\\USBRelayHost"
!define START_MENU_DIR "USB Relay IP"

Name "${PRODUCT_NAME} - Host"
OutFile "..\\dist\\USBRelayHost_Setup.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKLM "Software\\${COMPANY_NAME}\\${APP_NAME}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "..\\host\\assets\\icon.ico"
!define MUI_UNICON "..\\host\\assets\\icon.ico"

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

Section "USB Relay IP Host" SEC01
    SetShellVarContext all
    SetOutPath "$INSTDIR"
    ; Stop any previous headless instance before replacing files.
    ExecWait 'schtasks /End /TN USBRelayHostBoot'
    ExecWait 'taskkill /F /IM USBRelayHost.exe'
    File /r "..\\dist\\USBRelayHost\\*"

    IfFileExists "$PROGRAMFILES64\usbipd-win\usbipd.exe" usbipd_config 0
    FindFirst $0 $1 "$INSTDIR\\_internal\\usbipd-install\\usbipd-win*.msi"
    ${If} $1 != ""
        DetailPrint "Instalando usbipd-win..."
        ExecWait 'msiexec /i "$INSTDIR\_internal\usbipd-install\$1" /quiet /norestart' $2
        DetailPrint "usbipd-win install exit code: $2"
    ${Else}
        DetailPrint "Instalador usbipd-win nao encontrado em $INSTDIR\\_internal\\usbipd-install"
    ${EndIf}
    FindClose $0
usbipd_config:
    ; Keep usbipd as a normal Windows service managed by SCM.
    ExecWait 'sc config usbipd start= auto'
    ExecWait 'sc failureflag usbipd 1'
    ExecWait 'sc failure usbipd reset= 86400 actions= restart/60000/restart/60000/'
    ; Register the SYSTEM boot task so the Host starts without login.
    ExecWait '"$INSTDIR\USBRelayHost.exe" --register-headless'

    WriteUninstaller "$INSTDIR\\Uninstall.exe"
    WriteRegStr HKLM "Software\\${COMPANY_NAME}\\${APP_NAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "DisplayName" "${PRODUCT_NAME} - Host"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "UninstallString" '"$INSTDIR\\Uninstall.exe"'
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "Publisher" "${COMPANY_NAME}"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}" "InstallLocation" "$INSTDIR"

    CreateDirectory "$SMPROGRAMS\\${START_MENU_DIR}"
    CreateShortCut "$SMPROGRAMS\\${START_MENU_DIR}\\USB Relay IP Host.lnk" "$INSTDIR\\USBRelayHost.exe"
    CreateShortCut "$DESKTOP\\USB Relay IP Host.lnk" "$INSTDIR\\USBRelayHost.exe"
SectionEnd

Section "Uninstall"
    SetShellVarContext all
    ExecWait '"$INSTDIR\USBRelayHost.exe" --unregister-headless'
    ExecWait 'schtasks /End /TN USBRelayHostBoot'
    ExecWait 'taskkill /F /IM USBRelayHost.exe'
    ExecWait 'sc config usbipd start= demand'
    Delete "$DESKTOP\\USB Relay IP Host.lnk"
    Delete "$SMPROGRAMS\\${START_MENU_DIR}\\USB Relay IP Host.lnk"
    RMDir "$SMPROGRAMS\\${START_MENU_DIR}"
    DeleteRegKey HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}"
    DeleteRegKey HKLM "Software\\${COMPANY_NAME}\\${APP_NAME}"
    RMDir /r "$INSTDIR"
SectionEnd
