Unicode true

!include "LogicLib.nsh"

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
RequestExecutionLevel admin
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

    ; Shared state used by SYSTEM headless and the interactive GUI.
    ; DACL: SYSTEM + Administrators full control; installing user's SID modify.
    CreateDirectory "$COMMONAPPDATA\USBRelay"
    ; Upgrade bridge: SYSTEM cannot see the interactive user's legacy APPDATA.
    ; Never overwrite an existing shared file; the Python loader merges both
    ; copies by observed_at when the GUI later runs.
    IfFileExists "$COMMONAPPDATA\USBRelay\pnp_sessions.json" pnp_migration_done 0
    IfFileExists "$APPDATA\USBRelay\pnp_sessions.json" 0 pnp_migration_done
    CopyFiles /SILENT "$APPDATA\USBRelay\pnp_sessions.json" "$COMMONAPPDATA\USBRelay\pnp_sessions.json"
pnp_migration_done:
    InitPluginsDir
    File /oname=$PLUGINSDIR\set_shared_acl.ps1 "set_shared_acl.ps1"
    ExecWait 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\set_shared_acl.ps1" -TargetPath "$COMMONAPPDATA\USBRelay"' $0
    ${If} $0 != 0
        MessageBox MB_ICONSTOP|MB_OK "Falha ao proteger o estado compartilhado USBRelay (PowerShell exit $0). A instalação será interrompida."
        Abort
    ${EndIf}

    ; Install USBip driver (VHCI) only if not already present
    ReadRegDWORD $0 HKLM "SYSTEM\CurrentControlSet\Services\usbip2_ude" "Type"
    ${If} ${Errors}
        FindFirst $1 $2 "$INSTDIR\_internal\usbipd-install\USBip*.exe"
        ${If} $2 != ""
            DetailPrint "Instalando driver USBip (VHCI)..."
            ExecWait '"$INSTDIR\_internal\usbipd-install\$2" /VERYSILENT /COMPONENTS=main,client /SUPPRESSMSGBOXES /NORESTART /SP-' $3
            DetailPrint "USBip driver install exit code: $3"
            DeleteRegKey HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{199505b0-b93d-4521-a8c7-897818e0205a}_is1"
        ${Else}
            DetailPrint "Instalador USBip nao encontrado em $INSTDIR\_internal\usbipd-install"
        ${EndIf}
        FindClose $1
    ${Else}
        DetailPrint "Driver USBip VHCI ja instalado, pulando..."
    ${EndIf}

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
    ; Stop and remove both startup mechanisms before deleting binaries.
    ExecWait 'schtasks.exe /End /TN "USBRelayClientBoot"' $0
    ExecWait 'schtasks.exe /Delete /TN "USBRelayClientBoot" /F' $0
    DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "USBRelayClient"
    IfFileExists "$PROGRAMFILES64\\USBip\\unins000.exe" 0 +2
    ExecWait '"$PROGRAMFILES64\\USBip\\unins000.exe" /VERYSILENT /NORESTART'
    Delete "$DESKTOP\\USB Relay IP Client.lnk"
    Delete "$SMPROGRAMS\\${START_MENU_DIR}\\USB Relay IP Client.lnk"
    RMDir "$SMPROGRAMS\\${START_MENU_DIR}"
    DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${APP_NAME}"
    DeleteRegKey HKCU "Software\\${COMPANY_NAME}\\${APP_NAME}"
    RMDir /r "$INSTDIR"
SectionEnd
