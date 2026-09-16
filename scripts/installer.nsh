!include "nsDialogs.nsh"

!ifndef BUILD_UNINSTALLER
Var BackendVariant
Var BackendCpuRadio
Var BackendGpuRadio

!macro customHeader
LangString BackendTitle ${LANG_TRADCHINESE} "選擇運算方式"
LangString BackendSubtitle ${LANG_TRADCHINESE} "選擇要安裝的語音辨識版本。"
LangString BackendCpu ${LANG_TRADCHINESE} "CPU（預設，適用於所有電腦）"
LangString BackendGpu ${LANG_TRADCHINESE} "NVIDIA GPU（CUDA 12.6）"
LangString BackendHelp ${LANG_TRADCHINESE} "GPU 版需要相容的 NVIDIA 顯示卡與驅動，不支援 AMD／Intel GPU 加速。若不確定，請選 CPU。兩個版本都已包含在安裝程式內。"
LangString BackendFailure ${LANG_TRADCHINESE} "無法安裝所選版本。請關閉 VRC2T 後重新執行安裝程式。"
LangString BackendTitle ${LANG_ENGLISH} "Choose processing mode"
LangString BackendSubtitle ${LANG_ENGLISH} "Select the speech recognition backend to install."
LangString BackendCpu ${LANG_ENGLISH} "CPU (default, works on all computers)"
LangString BackendGpu ${LANG_ENGLISH} "NVIDIA GPU (CUDA 12.6)"
LangString BackendHelp ${LANG_ENGLISH} "GPU mode requires a compatible NVIDIA GPU and driver. AMD/Intel GPU acceleration is not supported. Choose CPU if unsure. Both versions are included in this installer."
LangString BackendFailure ${LANG_ENGLISH} "Could not install the selected backend. Close VRC2T and run the installer again."
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

!macro customInstall
  ${If} $BackendVariant == "cu126"
    ; The common app payload contains CPU. Replace that complete directory so
    ; CPU and CUDA native libraries can never be mixed. Only touch our backend.
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
    File /r "${PROJECT_DIR}\build\python-cu126\vrc-v2t-backend\*.*"
    ${If} ${Errors}
      MessageBox MB_OK|MB_ICONSTOP "$(BackendFailure)" /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "BackendVariant" "$BackendVariant"
  SetOutPath "$INSTDIR"
!macroend
!endif
