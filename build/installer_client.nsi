!include "MUI2.nsh"
!include "FileFunc.nsh"

!define MUI_ICON "..\client\assets\icon.ico"
!define MUI_UNICON "..\client\assets\icon.ico"

!define PRODUCT_NAME "USB Relay IP Client"
!define PRODUCT_VERSION "1.0.0"
!define PRODUCT_PUBLISHER "OhMyTech Solucoes Digitais"
!define PRODUCT_DIR_REGKEY "Software\USBRelayClient"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\USBRelayClient_Setup.exe"
InstallDir "$PROGRAMFILES64\USB Relay IP\Client"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "PortugueseBR"

Section "Install"
    SetRegView 64
    SetOutPath "$INSTDIR"
    File /r "..\dist\USBRelayClient\*"

    ; Install USBip driver (VHCI) only if not already present
    SetOutPath "$INSTDIR\_internal\usbipd-install"
    File "..\usbipd-install\USBip-0.9.7.7-x64.exe"

    ; Check if VHCI driver service already exists in registry
    ReadRegDWORD $0 HKLM "SYSTEM\CurrentControlSet\Services\usbip2_ude" "Type"
    ${If} ${Errors}
        DetailPrint "Instalando driver USBip (VHCI)..."
        ExecWait '"$INSTDIR\_internal\usbipd-install\USBip-0.9.7.7-x64.exe" /VERYSILENT /COMPONENTS=main,client /SUPPRESSMSGBOXES /NORESTART /SP-' $1
        DetailPrint "USBip driver install exit code: $1"
        ; Remove USBip from Control Panel (Programs and Features)
        SetRegView 64
        DeleteRegKey HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{199505b0-b93d-4521-a8c7-897818e0205a}_is1"
    ${Else}
        DetailPrint "Driver USBip VHCI ja instalado, pulando..."
    ${EndIf}

    CreateDirectory "$SMPROGRAMS\USB Relay IP"
    CreateShortCut "$SMPROGRAMS\USB Relay IP\USB Relay IP Client.lnk" "$INSTDIR\USBRelayClient.exe"
    CreateShortCut "$DESKTOP\USB Relay IP Client.lnk" "$INSTDIR\USBRelayClient.exe"

    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\USBRelayClient.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
    ; Uninstall USBip driver
    ExecWait '"$PROGRAMFILES64\USBip\unins000.exe" /VERYSILENT /NORESTART'

    Delete "$INSTDIR\Uninstall.exe"
    RMDir /r "$INSTDIR"

    Delete "$SMPROGRAMS\USB Relay IP\USB Relay IP Client.lnk"
    RMDir "$SMPROGRAMS\USB Relay IP"
    Delete "$DESKTOP\USB Relay IP Client.lnk"

    DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
    DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
SectionEnd
