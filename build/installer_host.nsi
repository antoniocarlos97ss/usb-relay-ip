!include "MUI2.nsh"
!include "FileFunc.nsh"

!define MUI_ICON "..\host\assets\icon.ico"
!define MUI_UNICON "..\host\assets\icon.ico"

!define PRODUCT_NAME "USB Relay IP Host"
!define PRODUCT_VERSION "1.0.0"
!define PRODUCT_PUBLISHER "OhMyTech Solucoes Digitais"
!define PRODUCT_DIR_REGKEY "Software\USBRelayHost"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\USBRelayHost_Setup.exe"
InstallDir "$PROGRAMFILES64\USB Relay IP\Host"
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
    SetOutPath "$INSTDIR"

    File /r "..\dist\USBRelayHost\*"

    CreateDirectory "$SMPROGRAMS\USB Relay IP"
    CreateShortCut "$SMPROGRAMS\USB Relay IP\USB Relay IP Host.lnk" "$INSTDIR\USBRelayHost.exe"
    CreateShortCut "$DESKTOP\USB Relay IP Host.lnk" "$INSTDIR\USBRelayHost.exe"

    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\USBRelayHost.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"

    IfFileExists "$INSTDIR\_internal\usbipd-install\usbipd-win_5.3.0_x64.msi" 0 skip_msi
        ExecWait 'msiexec /i "$INSTDIR\_internal\usbipd-install\usbipd-win_5.3.0_x64.msi" /quiet /norestart'
    skip_msi:
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir /r "$INSTDIR"

    Delete "$SMPROGRAMS\USB Relay IP\USB Relay IP Host.lnk"
    RMDir "$SMPROGRAMS\USB Relay IP"
    Delete "$DESKTOP\USB Relay IP Host.lnk"

    DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
    DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
SectionEnd
