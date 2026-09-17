; electron-builder loads this include before its own MUI2 include. Page
; functions below expand MUI macros immediately, so load the dependency here.
!include "MUI2.nsh"
!include "nsDialogs.nsh"
; Guarded header; electron-builder's own template includes it again later.
!include "FileFunc.nsh"

!ifndef BUILD_UNINSTALLER
; Archive names, download URLs, SHA2-512 digests and sizes, written by
; scripts/prepare-assets.cjs. Run `pnpm prepare:assets` before packaging.
; Each asset carries its own URL: the backends live under this release's tag,
; while the emotion model stays on the tag it was published under once.
!include "${PROJECT_DIR}\build\installer-payload\installer-assets.nsh"

Var BackendVariant
Var BackendCpuRadio
Var BackendGpuRadio
Var EmotionWanted
Var EmotionCheckbox
; The asset currently being fetched, so one pair of macros serves all of them.
Var AssetArchive
Var AssetUrl
Var AssetEntry
Var AssetDigest
Var AssetRequiredMb
Var AssetFile

!macro customHeader
LangString BackendTitle ${LANG_TRADCHINESE} "選擇要安裝的元件"
LangString BackendSubtitle ${LANG_TRADCHINESE} "選擇語音辨識版本，以及是否安裝情緒辨識模型。"
LangString BackendCpu ${LANG_TRADCHINESE} "CPU（預設，適用於所有電腦）－ 下載約 ${ASSET_CPU_DOWNLOAD_MB} MB"
LangString BackendGpu ${LANG_TRADCHINESE} "NVIDIA GPU（CUDA 12.6）－ 下載約 ${ASSET_CU126_DOWNLOAD_MB} MB"
LangString EmotionOption ${LANG_TRADCHINESE} "安裝情緒辨識模型－ 下載約 ${ASSET_EMOTION_DOWNLOAD_MB} MB"
LangString BackendHelp ${LANG_TRADCHINESE} "GPU 版需要相容的 NVIDIA 顯示卡與驅動，不支援 AMD／Intel GPU 加速。若不確定，請選 CPU。$\r$\n$\r$\n情緒辨識預設關閉，不安裝模型也能正常使用語音轉文字；之後想啟用，重新執行安裝程式勾選即可。$\r$\n$\r$\n所選元件會在安裝過程中下載，請保持網路連線。"
LangString BackendFailure ${LANG_TRADCHINESE} "無法安裝所選元件。請關閉 VRC2T 後重新執行安裝程式。"
LangString BackendDownloadFailure ${LANG_TRADCHINESE} "下載 $AssetArchive 失敗（狀態：$0）。$\r$\n$\r$\n請檢查網路連線後重試。"
LangString BackendDigestFailure ${LANG_TRADCHINESE} "下載的 $AssetArchive 毀損，校驗碼不符。$\r$\n$\r$\n請重試下載。"
LangString BackendSpaceFailure ${LANG_TRADCHINESE} "$INSTDIR 所在磁碟空間不足。安裝 $AssetArchive 需要約 $AssetRequiredMb MB，目前只剩 $0 MB。"
LangString BackendTitle ${LANG_ENGLISH} "Choose what to install"
LangString BackendSubtitle ${LANG_ENGLISH} "Select the speech recognition backend and whether to install the emotion model."
LangString BackendCpu ${LANG_ENGLISH} "CPU (default, works on all computers) - about ${ASSET_CPU_DOWNLOAD_MB} MB to download"
LangString BackendGpu ${LANG_ENGLISH} "NVIDIA GPU (CUDA 12.6) - about ${ASSET_CU126_DOWNLOAD_MB} MB to download"
LangString EmotionOption ${LANG_ENGLISH} "Install the emotion recognition model - about ${ASSET_EMOTION_DOWNLOAD_MB} MB to download"
LangString BackendHelp ${LANG_ENGLISH} "GPU mode requires a compatible NVIDIA GPU and driver. AMD/Intel GPU acceleration is not supported. Choose CPU if unsure.$\r$\n$\r$\nEmotion recognition is off by default and speech to text works without its model. To enable it later, run this installer again and tick the box.$\r$\n$\r$\nThe selected components are downloaded during installation, so stay connected."
LangString BackendFailure ${LANG_ENGLISH} "Could not install the selected components. Close VRC2T and run the installer again."
LangString BackendDownloadFailure ${LANG_ENGLISH} "Could not download $AssetArchive (status: $0).$\r$\n$\r$\nPlease check your internet connection and retry."
LangString BackendDigestFailure ${LANG_ENGLISH} "The downloaded $AssetArchive is corrupt: its checksum does not match.$\r$\n$\r$\nPlease retry the download."
LangString BackendSpaceFailure ${LANG_ENGLISH} "Not enough space on the drive holding $INSTDIR. $AssetArchive needs about $AssetRequiredMb MB, but only $0 MB is free."
!macroend

!macro customInit
  ReadRegStr $BackendVariant SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "BackendVariant"
  ${If} $BackendVariant != "cu126"
    StrCpy $BackendVariant "cpu"
  ${EndIf}
  ReadRegStr $EmotionWanted SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "EmotionModel"
  ${If} $EmotionWanted != "yes"
    StrCpy $EmotionWanted "no"
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
  ClearErrors
  ${GetOptions} $R0 "/EMOTION=" $R1
  ${IfNot} ${Errors}
    ${If} $R1 != "yes"
    ${AndIf} $R1 != "no"
      MessageBox MB_OK|MB_ICONSTOP "Invalid /EMOTION value. Use yes or no." /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}
    StrCpy $EmotionWanted $R1
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
  ${NSD_CreateRadioButton} 0 4u 100% 12u "$(BackendCpu)"
  Pop $BackendCpuRadio
  ${NSD_CreateRadioButton} 0 20u 100% 12u "$(BackendGpu)"
  Pop $BackendGpuRadio
  ${NSD_CreateCheckBox} 0 40u 100% 12u "$(EmotionOption)"
  Pop $EmotionCheckbox
  ${NSD_CreateLabel} 0 58u 100% 74u "$(BackendHelp)"
  Pop $0
  ${If} $BackendVariant == "cu126"
    ${NSD_Check} $BackendGpuRadio
  ${Else}
    ${NSD_Check} $BackendCpuRadio
  ${EndIf}
  ${If} $EmotionWanted == "yes"
    ${NSD_Check} $EmotionCheckbox
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
  ${NSD_GetState} $EmotionCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $EmotionWanted "yes"
  ${Else}
    StrCpy $EmotionWanted "no"
  ${EndIf}
FunctionEnd

; These are macros, not functions: electron-builder appends this include ahead
; of its own !addplugindir, so anything parsed at include time cannot call the
; StdUtils, INetC or nsExec plugins. Expanding inside customInstall defers the
; parse to the install section, which is what the templates themselves do.

!macro selectAsset NAME URL ENTRY HASH REQUIRED_MB
  StrCpy $AssetArchive "${NAME}"
  StrCpy $AssetUrl "${URL}"
  StrCpy $AssetEntry "${ENTRY}"
  StrCpy $AssetDigest "${HASH}"
  StrCpy $AssetRequiredMb "${REQUIRED_MB}"
!macroend

; Leaves a verified archive path in $AssetFile, or quits with error level 2.
; ID only has to make the retry label unique across insertions.
!macro obtainAsset ID
  ; An archive placed next to the installer allows an offline or mirrored
  ; install. Accept it only when it matches the digest compiled into this
  ; installer, so it can never be an archive built for another version.
  StrCpy $AssetFile "$EXEDIR\$AssetArchive"
  StrCpy $R1 "download"
  ${If} ${FileExists} "$AssetFile"
    ${StdUtils.HashFile} $R0 "SHA2-512" "$AssetFile"
    ${If} $R0 == $AssetDigest
      DetailPrint "Using $AssetArchive from the installer directory"
      StrCpy $R1 "local"
    ${Else}
      DetailPrint "Ignoring $AssetArchive next to the installer: checksum does not match"
    ${EndIf}
  ${EndIf}

  ${If} $R1 == "download"
    StrCpy $AssetFile "$PLUGINSDIR\$AssetArchive"
    ${ID}Download:
      DetailPrint "Downloading $AssetArchive"
      inetc::get /USERAGENT "electron-builder (Mozilla)" /RESUME "" "$AssetUrl" "$AssetFile" /END
      Pop $0
      ${If} $0 != "OK"
      ${AndIf} $0 != "Cancelled"
        ; Retry without the system proxy, the way the app package download does.
        inetc::get /NOPROXY /USERAGENT "electron-builder (Mozilla)" /RESUME "" "$AssetUrl" "$AssetFile" /END
        Pop $0
      ${EndIf}
      ${If} $0 == "Cancelled"
        SetErrorLevel 2
        Quit
      ${EndIf}
      ${If} $0 != "OK"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(BackendDownloadFailure)" /SD IDCANCEL IDRETRY ${ID}Download
        SetErrorLevel 2
        Quit
      ${EndIf}

      ${StdUtils.HashFile} $R0 "SHA2-512" "$AssetFile"
      ${If} $R0 != $AssetDigest
        ; A resumed download would keep the damaged bytes, so start over.
        Delete "$AssetFile"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(BackendDigestFailure)" /SD IDCANCEL IDRETRY ${ID}Download
        SetErrorLevel 2
        Quit
      ${EndIf}
  ${EndIf}
!macroend

!macro stageExtractor
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
!macroend

; Replaces TARGET wholesale, so a CPU and a CUDA backend can never be mixed and
; a stale model can never survive. Only ever touches directories we own.
!macro extractAsset TARGET
  ${GetRoot} "$INSTDIR" $R0
  ${DriveSpace} "$R0\" "/D=F /S=M" $0
  ${If} $0 < $AssetRequiredMb
    MessageBox MB_OK|MB_ICONSTOP "$(BackendSpaceFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}

  SetOutPath "$INSTDIR"
  ; A fresh install has nothing here, and RMDir flags an error on a missing
  ; directory, so only clear one that is actually there.
  ${If} ${FileExists} "${TARGET}\*.*"
    ClearErrors
    RMDir /r "${TARGET}"
    ${If} ${Errors}
      MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}

  SetOutPath "${TARGET}"
  ClearErrors
  nsExec::ExecToLog '"$PLUGINSDIR\7za.exe" x "$AssetFile" -o"${TARGET}" -y'
  Pop $0
  ${If} ${Errors}
  ${OrIf} $0 != "0"
  ${OrIfNot} ${FileExists} "${TARGET}\$AssetEntry"
    MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  SetOutPath "$INSTDIR"
  Delete "$PLUGINSDIR\$AssetArchive"
!macroend

!macro customInstall
  InitPluginsDir
  !insertmacro stageExtractor

  ${If} $BackendVariant == "cu126"
    !insertmacro selectAsset "${ASSET_CU126_NAME}" "${ASSET_CU126_URL}" "${ASSET_CU126_ENTRY}" "${ASSET_CU126_HASH}" "${ASSET_CU126_REQUIRED_MB}"
  ${Else}
    !insertmacro selectAsset "${ASSET_CPU_NAME}" "${ASSET_CPU_URL}" "${ASSET_CPU_ENTRY}" "${ASSET_CPU_HASH}" "${ASSET_CPU_REQUIRED_MB}"
  ${EndIf}
  ; Download before removing anything, so a failed download cannot destroy a
  ; working installation that is only being repaired or switched.
  !insertmacro obtainAsset "Backend"
  !insertmacro extractAsset "$INSTDIR\resources\backend"

  ${If} $EmotionWanted == "yes"
    !insertmacro selectAsset "${ASSET_EMOTION_NAME}" "${ASSET_EMOTION_URL}" "${ASSET_EMOTION_ENTRY}" "${ASSET_EMOTION_HASH}" "${ASSET_EMOTION_REQUIRED_MB}"
    !insertmacro obtainAsset "Emotion"
    !insertmacro extractAsset "$INSTDIR\resources\models\emotion"
  ${Else}
    ; Reinstalling with the box cleared removes a previously installed model.
    RMDir /r "$INSTDIR\resources\models\emotion"
    RMDir "$INSTDIR\resources\models"
  ${EndIf}

  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "BackendVariant" "$BackendVariant"
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "EmotionModel" "$EmotionWanted"
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
