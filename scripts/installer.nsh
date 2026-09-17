; electron-builder loads this include before its own MUI2 include. Page
; functions below expand MUI macros immediately, so load the dependency here.
!include "MUI2.nsh"
!include "nsDialogs.nsh"
; Guarded header; electron-builder's own template includes it again later.
!include "FileFunc.nsh"

!ifndef BUILD_UNINSTALLER
; Archive names, SHA2-512 digests, sizes and the release base URL, written by
; scripts/prepare-backends.cjs. Run `pnpm prepare:backends` before packaging.
!include "${PROJECT_DIR}\build\installer-payload\backend-assets.nsh"

Var BackendVariant
Var BackendCpuRadio
Var BackendGpuRadio
Var BackendArchive
Var BackendDigest
Var BackendRequiredMb
Var BackendFile

!macro customHeader
LangString BackendTitle ${LANG_TRADCHINESE} "選擇運算方式"
LangString BackendSubtitle ${LANG_TRADCHINESE} "選擇要下載並安裝的語音辨識版本。"
LangString BackendCpu ${LANG_TRADCHINESE} "CPU（預設，適用於所有電腦）－ 下載約 ${BACKEND_CPU_DOWNLOAD_MB} MB"
LangString BackendGpu ${LANG_TRADCHINESE} "NVIDIA GPU（CUDA 12.6）－ 下載約 ${BACKEND_CU126_DOWNLOAD_MB} MB"
LangString BackendHelp ${LANG_TRADCHINESE} "GPU 版需要相容的 NVIDIA 顯示卡與驅動，不支援 AMD／Intel GPU 加速。若不確定，請選 CPU。所選版本會在安裝過程中下載，請保持網路連線。"
LangString BackendFailure ${LANG_TRADCHINESE} "無法安裝所選版本。請關閉 VRC2T 後重新執行安裝程式。"
LangString BackendDownloadFailure ${LANG_TRADCHINESE} "下載語音辨識後端失敗（狀態：$0）。$\r$\n$\r$\n請檢查網路連線後重試。"
LangString BackendDigestFailure ${LANG_TRADCHINESE} "下載的語音辨識後端檔案毀損，校驗碼不符。$\r$\n$\r$\n請重試下載。"
LangString BackendSpaceFailure ${LANG_TRADCHINESE} "$INSTDIR 所在磁碟空間不足。安裝所選版本需要約 $BackendRequiredMb MB，目前只剩 $0 MB。"
LangString BackendTitle ${LANG_ENGLISH} "Choose processing mode"
LangString BackendSubtitle ${LANG_ENGLISH} "Select the speech recognition backend to download and install."
LangString BackendCpu ${LANG_ENGLISH} "CPU (default, works on all computers) - about ${BACKEND_CPU_DOWNLOAD_MB} MB to download"
LangString BackendGpu ${LANG_ENGLISH} "NVIDIA GPU (CUDA 12.6) - about ${BACKEND_CU126_DOWNLOAD_MB} MB to download"
LangString BackendHelp ${LANG_ENGLISH} "GPU mode requires a compatible NVIDIA GPU and driver. AMD/Intel GPU acceleration is not supported. Choose CPU if unsure. The selected version is downloaded during installation, so stay connected."
LangString BackendFailure ${LANG_ENGLISH} "Could not install the selected backend. Close VRC2T and run the installer again."
LangString BackendDownloadFailure ${LANG_ENGLISH} "Could not download the speech recognition backend (status: $0).$\r$\n$\r$\nPlease check your internet connection and retry."
LangString BackendDigestFailure ${LANG_ENGLISH} "The downloaded speech recognition backend is corrupt: its checksum does not match.$\r$\n$\r$\nPlease retry the download."
LangString BackendSpaceFailure ${LANG_ENGLISH} "Not enough space on the drive holding $INSTDIR. The selected backend needs about $BackendRequiredMb MB, but only $0 MB is free."
!macroend

!macro customInit
  ReadRegStr $BackendVariant SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "BackendVariant"
  ${If} $BackendVariant != "cu126"
    StrCpy $BackendVariant "cpu"
  ${EndIf}
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/BACKEND=" $R1
  ${IfNot} ${Errors}
    ${If} $R1 != "cpu"
    ${AndIf} $R1 != "cu126"
      MessageBox MB_OK|MB_ICONSTOP "Invalid /BACKEND value. Use cpu or cu126." /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}
    StrCpy $BackendVariant $R1
  ${EndIf}
!macroend

!macro customPageAfterChangeDir
  Page custom BackendPageCreate BackendPageLeave
!macroend

Function BackendPageCreate
  !insertmacro MUI_HEADER_TEXT "$(BackendTitle)" "$(BackendSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateRadioButton} 0 8u 100% 18u "$(BackendCpu)"
  Pop $BackendCpuRadio
  ${NSD_CreateRadioButton} 0 36u 100% 18u "$(BackendGpu)"
  Pop $BackendGpuRadio
  ${NSD_CreateLabel} 0 68u 100% 60u "$(BackendHelp)"
  Pop $0
  ${If} $BackendVariant == "cu126"
    ${NSD_Check} $BackendGpuRadio
  ${Else}
    ${NSD_Check} $BackendCpuRadio
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function BackendPageLeave
  ${NSD_GetState} $BackendGpuRadio $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $BackendVariant "cu126"
  ${Else}
    StrCpy $BackendVariant "cpu"
  ${EndIf}
FunctionEnd

; These are macros, not functions: electron-builder appends this include ahead
; of its own !addplugindir, so anything parsed at include time cannot call the
; StdUtils, INetC or nsExec plugins. Expanding inside customInstall defers the
; parse to the install section, which is what the templates themselves do.
!macro selectBackendAsset
  ${If} $BackendVariant == "cu126"
    StrCpy $BackendArchive "${BACKEND_CU126_NAME}"
    StrCpy $BackendDigest "${BACKEND_CU126_HASH}"
    StrCpy $BackendRequiredMb "${BACKEND_CU126_REQUIRED_MB}"
  ${Else}
    StrCpy $BackendArchive "${BACKEND_CPU_NAME}"
    StrCpy $BackendDigest "${BACKEND_CPU_HASH}"
    StrCpy $BackendRequiredMb "${BACKEND_CPU_REQUIRED_MB}"
  ${EndIf}
!macroend

; Leaves a verified archive path in $BackendFile, or quits with error level 2.
!macro obtainBackendArchive
  ; An archive placed next to the installer allows an offline or mirrored
  ; install. Accept it only when it matches the digest compiled into this
  ; installer, so it can never be an archive built for another version.
  StrCpy $BackendFile "$EXEDIR\$BackendArchive"
  StrCpy $R1 "download"
  ${If} ${FileExists} "$BackendFile"
    ${StdUtils.HashFile} $R0 "SHA2-512" "$BackendFile"
    ${If} $R0 == $BackendDigest
      DetailPrint "Using $BackendArchive from the installer directory"
      StrCpy $R1 "local"
    ${Else}
      DetailPrint "Ignoring $BackendArchive next to the installer: checksum does not match"
    ${EndIf}
  ${EndIf}

  ${If} $R1 == "download"
    StrCpy $BackendFile "$PLUGINSDIR\$BackendArchive"
    BackendDownload:
      DetailPrint "Downloading $BackendArchive"
      inetc::get /USERAGENT "electron-builder (Mozilla)" /RESUME "" "${BACKEND_BASE_URL}/$BackendArchive" "$BackendFile" /END
      Pop $0
      ${If} $0 != "OK"
      ${AndIf} $0 != "Cancelled"
        ; Retry without the system proxy, the way the app package download does.
        inetc::get /NOPROXY /USERAGENT "electron-builder (Mozilla)" /RESUME "" "${BACKEND_BASE_URL}/$BackendArchive" "$BackendFile" /END
        Pop $0
      ${EndIf}
      ${If} $0 == "Cancelled"
        SetErrorLevel 2
        Quit
      ${EndIf}
      ${If} $0 != "OK"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(BackendDownloadFailure)" /SD IDCANCEL IDRETRY BackendDownload
        SetErrorLevel 2
        Quit
      ${EndIf}

      ${StdUtils.HashFile} $R0 "SHA2-512" "$BackendFile"
      ${If} $R0 != $BackendDigest
        ; A resumed download would keep the damaged bytes, so start over.
        Delete "$BackendFile"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(BackendDigestFailure)" /SD IDCANCEL IDRETRY BackendDownload
        SetErrorLevel 2
        Quit
      ${EndIf}
  ${EndIf}
!macroend

!macro installBackendArchive
  ${GetRoot} "$INSTDIR" $R0
  ${DriveSpace} "$R0\" "/D=F /S=M" $0
  ${If} $0 < $BackendRequiredMb
    MessageBox MB_OK|MB_ICONSTOP "$(BackendSpaceFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}

  SetOutPath "$PLUGINSDIR"
  ClearErrors
  File "${PROJECT_DIR}\build\installer-payload\7za.exe"
  File /oname=7zip-LICENSE.txt "${PROJECT_DIR}\build\installer-payload\LICENSE.txt"
  File /oname=7zip-COPYING "${PROJECT_DIR}\build\installer-payload\COPYING"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}

  ; Replace the complete directory so CPU and CUDA native libraries can never
  ; be mixed. Only ever touch our own backend directory.
  SetOutPath "$INSTDIR\resources"
  ClearErrors
  RMDir /r "$INSTDIR\resources\backend"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}

  SetOutPath "$INSTDIR\resources\backend"
  ClearErrors
  nsExec::ExecToLog '"$PLUGINSDIR\7za.exe" x "$BackendFile" -o"$INSTDIR\resources\backend" -y'
  Pop $0
  ${If} ${Errors}
  ${OrIf} $0 != "0"
  ${OrIfNot} ${FileExists} "$INSTDIR\resources\backend\${BACKEND_ENTRY_NAME}"
    MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  Delete "$PLUGINSDIR\$BackendArchive"
!macroend

!macro customInstall
  InitPluginsDir
  !insertmacro selectBackendAsset
  ; Download before removing anything, so a failed download cannot destroy a
  ; working installation that is only being repaired or switched.
  !insertmacro obtainBackendArchive
  !insertmacro installBackendArchive
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "BackendVariant" "$BackendVariant"
  SetOutPath "$INSTDIR"
  !ifdef APP_PACKAGE_STORE_FILE
    ; The web installer keeps the downloaded app package for electron-updater,
    ; which this app does not ship. Reclaim that space instead.
    ${If} $installMode == "all"
      SetShellVarContext current
    ${EndIf}
    Delete "$LOCALAPPDATA\${APP_PACKAGE_STORE_FILE}"
    ${If} $installMode == "all"
      SetShellVarContext all
    ${EndIf}
  !endif
!macroend
!endif
