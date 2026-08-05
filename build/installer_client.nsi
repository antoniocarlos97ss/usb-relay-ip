Unicode true

!include "LogicLib.nsh"

!define APP_NAME "USBRelayClient"
!define APP_VERSION "1.0.0"
!define COMPANY_NAME "OhMyTech"
!define PRODUCT_NAME "USB Relay IP"
; The executable named by the SYSTEM boot task must live under the default
; administrator-protected Program Files ACL, never in LocalAppData or source.
!define INSTALL_DIR "$PROGRAMFILES64\USBRelayClient"
!define START_MENU_DIR "USB Relay IP"

Var SharedStatePreexisted
Var SharedStateCreated
Var CleanupAttempted

Name "${PRODUCT_NAME} - Client"
OutFile "..\dist\USBRelayClient_Setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "..\client\assets\icon.ico"
!define MUI_UNICON "..\client\assets\icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\README.md"
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
    ; Remove a legacy SYSTEM task before replacing an old LocalAppData install.
    ; The per-user HKCU Run value is intentionally left untouched; it is not a
    ; SYSTEM boundary and the GUI can update it after the protected upgrade.
    ExecWait 'schtasks.exe /End /TN "USBRelayClientBoot"' $0
    ExecWait 'schtasks.exe /Delete /TN "USBRelayClientBoot" /F' $0

    SetOutPath "$INSTDIR"
    File /r "..\dist\USBRelayClient\*"

    ; Extract once so abort cleanup can use the same fail-closed helper.
    InitPluginsDir
    File /oname=$PLUGINSDIR\set_shared_acl.ps1 "set_shared_acl.ps1"

    ; Shared state used by SYSTEM headless and the interactive GUI.
    ; DACL: SYSTEM + Administrators full control; installing user's SID modify.
    ; Record ownership before creating the directory so a failed first install
    ; can remove only the root created by this installer.
    StrCpy $SharedStatePreexisted "0"
    StrCpy $SharedStateCreated "0"
    StrCpy $CleanupAttempted "0"
    IfFileExists "$COMMONAPPDATA\USBRelay\." shared_state_exists shared_state_missing
shared_state_exists:
    StrCpy $SharedStatePreexisted "1"
    Goto shared_state_ready
shared_state_missing:
    CreateDirectory "$COMMONAPPDATA\USBRelay"
    StrCpy $SharedStateCreated "1"
shared_state_ready:

    ; set_shared_acl.ps1 protects the target and only then performs the optional
    ; exact-file legacy migration. It rejects reparse points before any walk.
    ExecWait 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\set_shared_acl.ps1" -TargetPath "$COMMONAPPDATA\USBRelay" -LegacyRoot "$APPDATA\USBRelay"' $0
    ${If} $0 != 0
        MessageBox MB_ICONSTOP|MB_OK "Falha ao proteger o estado compartilhado USBRelay (PowerShell exit $0). A instalação será interrompida com cleanup seguro."
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

    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\${COMPANY_NAME}\${APP_NAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${PRODUCT_NAME} - Client"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${COMPANY_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"

    CreateDirectory "$SMPROGRAMS\${START_MENU_DIR}"
    CreateShortCut "$SMPROGRAMS\${START_MENU_DIR}\USB Relay IP Client.lnk" "$INSTDIR\USBRelayClient.exe"
    CreateShortCut "$DESKTOP\USB Relay IP Client.lnk" "$INSTDIR\USBRelayClient.exe"
SectionEnd

; Only remove the newly-created ProgramData\USBRelay root. Existing state is
; never deleted as an install rollback side effect, and the helper rejects
; junctions before it removes anything.
Function CleanupPartialInstall
    ${If} $CleanupAttempted == "1"
        Return
    ${EndIf}
    StrCpy $CleanupAttempted "1"
    ${If} $SharedStateCreated == "1"
        IfFileExists "$PLUGINSDIR\set_shared_acl.ps1" 0 cleanup_partial_done
        ExecWait 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\set_shared_acl.ps1" -TargetPath "$COMMONAPPDATA\USBRelay" -RemoveTarget' $0
        ${If} $0 != 0
            DetailPrint "Cleanup seguro do estado compartilhado não removeu a raiz (exit $0); nenhum caminho externo será tocado."
        ${EndIf}
    ${EndIf}
cleanup_partial_done:
FunctionEnd

Function .onInstFailed
    Call CleanupPartialInstall
FunctionEnd

Function .onUserAbort
    Call CleanupPartialInstall
FunctionEnd

Section "Uninstall"
    SetShellVarContext current
    ; Stop and remove both startup mechanisms before deleting binaries.
    ExecWait 'schtasks.exe /End /TN "USBRelayClientBoot"' $0
    ExecWait 'schtasks.exe /Delete /TN "USBRelayClientBoot" /F' $0
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "USBRelayClient"
    IfFileExists "$PROGRAMFILES64\USBip\unins000.exe" 0 +2
    ExecWait '"$PROGRAMFILES64\USBip\unins000.exe" /VERYSILENT /NORESTART'

    ; Shared state can contain the API key. Interactive uninstall asks
    ; explicitly; /S (silent) deterministically preserves it and reports that
    ; choice in the installer log. Removal is limited by the helper to the
    ; exact ProgramData\USBRelay root and refuses reparse points.
    IfSilent keep_shared_state
    MessageBox MB_ICONQUESTION|MB_YESNO "Remover o estado compartilhado USBRelay, incluindo a chave de API? Escolha Não para preservar configurações." IDYES remove_shared_state IDNO keep_shared_state
remove_shared_state:
    InitPluginsDir
    File /oname=$PLUGINSDIR\set_shared_acl.ps1 "set_shared_acl.ps1"
    ExecWait 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\set_shared_acl.ps1" -TargetPath "$COMMONAPPDATA\USBRelay" -RemoveTarget' $1
    ${If} $1 != 0
        MessageBox MB_ICONEXCLAMATION|MB_OK "O estado compartilhado não foi removido (PowerShell exit $1); a chave de API foi preservada por segurança."
    ${EndIf}
    Goto shared_state_cleanup_done
keep_shared_state:
    DetailPrint "Estado compartilhado USBRelay preservado (incluindo a chave de API); /S usa este comportamento."
shared_state_cleanup_done:

    Delete "$DESKTOP\USB Relay IP Client.lnk"
    Delete "$SMPROGRAMS\${START_MENU_DIR}\USB Relay IP Client.lnk"
    RMDir "$SMPROGRAMS\${START_MENU_DIR}"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKCU "Software\${COMPANY_NAME}\${APP_NAME}"
    RMDir /r "$INSTDIR"
SectionEnd
