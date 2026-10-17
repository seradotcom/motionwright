; Derived from the Tauri NSIS template at 30da1fd6e17de6107ecc850c95dfb16b5729f2dd.
; Upstream SHA256: dabed59013b1d78b879a1a85bc7f2eed2993b33a9a90cdabe5946de3d3950597
; The upstream template is used under its MIT license:
; MIT License
;
; Copyright (c) 2017 - Present Tauri Apps Contributors
;
; Permission is hereby granted, free of charge, to any person obtaining a copy
; of this software and associated documentation files (the "Software"), to deal
; in the Software without restriction, including without limitation the rights
; to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
; copies of the Software, and to permit persons to whom the Software is
; furnished to do so, subject to the following conditions:
;
; The above copyright notice and this permission notice shall be included in all
; copies or substantial portions of the Software.
;
; THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
; IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
; FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
; AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
; LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
; OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
; SOFTWARE.
;
Unicode true
ManifestDPIAware true
; Add in `dpiAwareness` `PerMonitorV2` to manifest for Windows 10 1607+ (note this should not affect lower versions since they should be able to ignore this and pick up `dpiAware` `true` set by `ManifestDPIAware true`)
; Currently undocumented on NSIS's website but is in the Docs folder of source tree, see
; https://github.com/kichik/nsis/blob/5fc0b87b819a9eec006df4967d08e522ddd651c9/Docs/src/attributes.but#L286-L300
; https://github.com/tauri-apps/tauri/pull/10106
ManifestDPIAwareness PerMonitorV2

!if "{{compression}}" == "none"
  SetCompress off
!else
  ; Set the compression algorithm. We default to LZMA.
  SetCompressor /SOLID "{{compression}}"
!endif

; Keep above !include to stay ahead of any plugin command
; see https://github.com/tauri-apps/tauri/pull/15422#discussion_r3289239624
{{#if signed_plugins_path}}
!addplugindir "{{signed_plugins_path}}"
{{/if}}

!include MUI2.nsh
!include FileFunc.nsh
!include x64.nsh
!include WordFunc.nsh
!include "utils.nsh"
!include "FileAssociation.nsh"
!include "Win\COM.nsh"
!include "Win\Propkey.nsh"
!include "Win\RestartManager.nsh"

{{#if installer_hooks}}
!include "{{installer_hooks}}"
{{else}}
!error "The ownership guard must be configured as bundle.windows.nsis.installerHooks."
{{/if}}
!ifndef MOTIONWRIGHT_UNINSTALL_OWNERSHIP_NSH
!error "The configured installer hooks do not contain the ownership guard."
!endif
!ifndef NSIS_WIN32_MAKENSIS
!error "This installer capture requires Windows MakeNSIS and Python 3."
!endif

!define WEBVIEW2APPGUID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

!define MANUFACTURER "{{manufacturer}}"
!define PRODUCTNAME "{{product_name}}"
!define VERSION "{{version}}"
!define VERSIONWITHBUILD "{{version_with_build}}"
!define HOMEPAGE "{{homepage}}"
!define INSTALLMODE "{{install_mode}}"
!define LICENSE "{{license}}"
!define INSTALLERICON "{{installer_icon}}"
!define SIDEBARIMAGE "{{sidebar_image}}"
!define HEADERIMAGE "{{header_image}}"
!define UNINSTALLERICON "{{uninstaller_icon}}"
!define UNINSTALLERHEADERIMAGE "{{uninstaller_header_image}}"
!define MAINBINARYNAME "{{main_binary_name}}"
!define MAINBINARYSRCPATH "{{main_binary_path}}"
!define BUNDLEID "{{bundle_id}}"
!define COPYRIGHT "{{copyright}}"
!define OUTFILE "{{out_file}}"
!define ARCH "{{arch}}"
!define ADDITIONALPLUGINSPATH "{{additional_plugins_path}}"
!define ALLOWDOWNGRADES "{{allow_downgrades}}"
!define DISPLAYLANGUAGESELECTOR "{{display_language_selector}}"
!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"
!define WEBVIEW2INSTALLERARGS "{{webview2_installer_args}}"
!define WEBVIEW2BOOTSTRAPPERPATH "{{webview2_bootstrapper_path}}"
!define WEBVIEW2INSTALLERPATH "{{webview2_installer_path}}"
!define MINIMUMWEBVIEW2VERSION "{{minimum_webview2_version}}"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}"
!define MANUKEY "Software\${MANUFACTURER}"
!define MANUPRODUCTKEY "${MANUKEY}\${PRODUCTNAME}"
!define UNINSTALLERSIGNCOMMAND "{{uninstaller_sign_cmd}}"
!define ESTIMATEDSIZE "{{estimated_size}}"
!define STARTMENUFOLDER "{{start_menu_folder}}"

; Capture each application payload in the fresh Tauri NSIS output directory.
; Its File instruction consumes only that capture. The include and capture helper never replace existing outputs.
!macro MWINST_CAPTURE Symbol Source
  !execute `"python.exe" "${MWOWN_BUILD_HELPER}" --source "${Source}" --capture "${__FILEDIR__}\mw-owned-${Symbol}.bin" --include "${__FILEDIR__}\mw-owned-${Symbol}.nsh" --symbol "${Symbol}"` = 0
  !include "${__FILEDIR__}\mw-owned-${Symbol}.nsh"
!macroend
!insertmacro MWINST_CAPTURE MW_MAIN "${MAINBINARYSRCPATH}"
{{#each resources}}
!if "{{@index}}" == ""
!error "The bundler must provide a numeric index for each resources entry."
!endif
!insertmacro MWINST_CAPTURE MW_RESOURCE_{{@index}} "{{@key}}"
{{/each}}
{{#each binaries}}
!if "{{@index}}" == ""
!error "The bundler must provide a numeric index for each binaries entry."
!endif
!insertmacro MWINST_CAPTURE MW_BINARY_{{@index}} "{{@key}}"
{{/each}}

; The optional predecessor profile is a separately pinned build input. Python
; reads its environment path directly; no environment text is shell-expanded here.
!execute `"python.exe" "${MWOWN_BUILD_HELPER}" --emit-predecessor --include "${__FILEDIR__}\mw-predecessor.nsh" --application "${PRODUCTNAME}" --bundle-id "${BUNDLEID}" --arch "${ARCH}" --target-version "${VERSION}" --target-main-sha256 "${MW_MAIN_SHA256}"` = 0
!include "${__FILEDIR__}\mw-predecessor.nsh"

; The extraction sections use SetOverwrite off as a non-replace policy.
; That policy and the adjacent CHECK calls do not form an install transaction.

!macro MWINST_FAIL Reason
  DetailPrint "Installation refused: ${Reason}"
  SetErrorLevel 2
  Abort "Installation stopped: ${Reason}"
!macroend

!macro MWINST_REQUIRE_ABSENT Relative Sha Size
  !insertmacro MWOWN_CHECK "" "$INSTDIR" "${Relative}" "${Sha}" "${Size}" $0
  ${If} $0 != ${MWOWN_MISSING}
    !insertmacro MWINST_FAIL "Destination is not absent: ${Relative}; $MWOWN_LastReason"
  ${EndIf}
!macroend

!macro MWINST_REQUIRE_MATCHED Relative Sha Size
  !insertmacro MWOWN_CHECK "" "$INSTDIR" "${Relative}" "${Sha}" "${Size}" $0
  ${If} $0 != ${MWOWN_MATCHED}
    !insertmacro MWINST_FAIL "Installed bytes were not verified: ${Relative}; $MWOWN_LastReason"
  ${EndIf}
!macroend

!macro MWINST_UNINSTALL_PREFLIGHT Relative Sha Size
  !insertmacro MWOWN_CHECK "un." "$INSTDIR" "${Relative}" "${Sha}" "${Size}" $0
  ${If} $0 != ${MWOWN_MATCHED}
  ${AndIf} $0 != ${MWOWN_MISSING}
    StrCpy $MWINST_UninstallPreserved 1
  ${EndIf}
!macroend

!macro MWINST_DELETE_OWNED Relative Sha Size
  !insertmacro MWOWN_DELETE "un." "$INSTDIR" "${Relative}" "${Sha}" "${Size}" $0
  ${If} $0 = ${MWOWN_DELETE_UNCERTAIN}
    StrCpy $MWINST_UninstallUncertain 1
    SetErrorLevel 3
    Abort "Uninstall outcome is uncertain. No operation was retried; registration was retained."
  ${ElseIf} $0 != ${MWOWN_DELETE_MARKED}
  ${AndIf} $0 != ${MWOWN_MISSING}
    StrCpy $MWINST_UninstallPreserved 1
    SetErrorLevel 2
    Abort "Uninstall stopped after a preservation decision. Registration was retained."
  ${EndIf}
!macroend

Var PassiveMode
Var UpdateMode
Var NoShortcutMode

!macro MWINST_CURRENT_ENTRY Relative Sha Size
  ${If} $MWINST_Relative == "${Relative}"
    StrCpy $MWINST_CurrentFound 1
    StrCpy $MWINST_CurrentSha "${Sha}"
    StrCpy $MWINST_CurrentSize "${Size}"
  ${EndIf}
!macroend

!macro MWINST_PREFLIGHT_PREVIOUS Relative Sha Size
  StrCpy $MWINST_Relative "${Relative}"
  Call MWINST_Classify
!macroend

!macro MWINST_REMOVE_OBSOLETE Relative Sha Size
  StrCpy $MWINST_Relative "${Relative}"
  Call MWINST_LookupCurrent
  ${If} $MWINST_CurrentFound = 0
    Call MWINST_DeletePrevious
  ${EndIf}
!macroend

!macro MWINST_PREPARE_PAYLOAD Symbol
  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "mw-prepared-${Symbol}.bin" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
  ${If} $0 != ${MWOWN_MISSING}
    !insertmacro MWINST_FAIL "The payload staging name is not absent."
  ${EndIf}
  ClearErrors
  File "/oname=$MWINST_PluginRoot\mw-prepared-${Symbol}.bin" "${${Symbol}_SOURCE}"
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "Payload preparation failed."
  ${EndIf}
  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "mw-prepared-${Symbol}.bin" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
  ${If} $0 != ${MWOWN_MATCHED}
    !insertmacro MWINST_FAIL "Prepared payload bytes were not verified; $MWOWN_LastReason"
  ${EndIf}
!macroend

!macro MWINST_INSTALL_PAYLOAD Relative Symbol
  ; First classify the existing immutable destination. A matching current
  ; payload needs neither an extraction nor another publication.
  StrCpy $MWINST_Relative "${Relative}"
  Call MWINST_Classify
  ${If} $MWINST_Classification != "NEW"
    ; Stage in the exact DESTINATION parent on the same volume. Extraction
    ; may be interrupted but never writes the canonical basename.
    !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "mw-prepared-${Symbol}.bin" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
    ${If} $0 != ${MWOWN_MATCHED}
      !insertmacro MWINST_FAIL "Prepared source changed before staging; $MWOWN_LastReason"
    ${EndIf}
    ${GetParent} "$INSTDIR\${Relative}" $MWINST_Parent
    ${GetFileName} "$INSTDIR\${Relative}" $MWINST_Leaf
    StrCpy $MWINST_StageLeaf "mw-stage-${Symbol}-${${Symbol}_SHA256}.bin"
    !insertmacro MWOWN_CHECK "" "$MWINST_Parent" "$MWINST_StageLeaf" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
    ${If} $0 = ${MWOWN_MISSING}
      ClearErrors
      File "/oname=$MWINST_Parent\$MWINST_StageLeaf" "${${Symbol}_SOURCE}"
      ${If} ${Errors}
        !insertmacro MWINST_FAIL "Destination-volume payload staging failed: ${Relative}"
      ${EndIf}
    ${ElseIf} $0 != ${MWOWN_MATCHED}
      ; Do not replace/delete an old partial or modified staging file.
      !insertmacro MWINST_FAIL "Untrusted payload staging bytes retained: ${Relative}; $MWOWN_LastReason"
    ${EndIf}
    !insertmacro MWOWN_CHECK "" "$MWINST_Parent" "$MWINST_StageLeaf" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
    ${If} $0 != ${MWOWN_MATCHED}
      !insertmacro MWINST_FAIL "Staged destination-volume bytes are not complete: ${Relative}; $MWOWN_LastReason"
    ${EndIf}

    ; Only after complete same-volume staging may the confirmed predecessor
    ; be withdrawn. The PUBLISH function reopens and checks the SAME staged
    ; handle through the atomic no-replace rename; it never runs File on
    ; the canonical destination.
    StrCpy $MWINST_Relative "${Relative}"
    Call MWINST_MakeDestinationAvailable
    ${If} $MWINST_NeedFile = 1
      !insertmacro MWINST_REQUIRE_ABSENT "${Relative}" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}"
      !insertmacro MWOWN_PUBLISH "$MWINST_Parent" "$MWINST_StageLeaf" "$MWINST_Leaf" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}" $0
      ${If} $0 = ${MWOWN_PUBLISH_UNCERTAIN}
        SetErrorLevel 3
        Abort "Publication outcome uncertain for ${Relative}; registration retained, no retry."
      ${ElseIf} $0 != ${MWOWN_PUBLISHED}
        !insertmacro MWINST_FAIL "Verified publication rejected for ${Relative}; $MWOWN_LastReason"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  !insertmacro MWINST_REQUIRE_MATCHED "${Relative}" "${${Symbol}_SHA256}" "${${Symbol}_SIZE}"
!macroend

Var MWINST_PluginRoot
Var MWINST_Parent
Var MWINST_Leaf
Var MWINST_NewSelfSha
Var MWINST_NewSelfSize
Var MWINST_Completed
Var MWINST_UninstallPreserved
Var MWINST_UninstallUncertain
Var MWINST_ShortcutSource
Var MWINST_ShortcutTarget
Var MWINST_ShortcutRoot
Var MWINST_ShortcutRelative
Var MWINST_Relative
Var MWINST_CurrentFound
Var MWINST_CurrentSha
Var MWINST_CurrentSize
Var MWINST_PrevFound
Var MWINST_PrevSha
Var MWINST_PrevSize
Var MWINST_Classification
Var MWINST_NeedFile
Var MWINST_StageLeaf

Name "${PRODUCTNAME}"
BrandingText "${COPYRIGHT}"
OutFile "${OUTFILE}"

; We don't actually use this value as default install path,
; it's just for nsis to append the product name folder in the directory selector
; https://nsis.sourceforge.io/Reference/InstallDir
!define PLACEHOLDER_INSTALL_DIR "placeholder\${PRODUCTNAME}"
InstallDir "${PLACEHOLDER_INSTALL_DIR}"

VIProductVersion "${VERSIONWITHBUILD}"
VIAddVersionKey "ProductName" "${PRODUCTNAME}"
VIAddVersionKey "FileDescription" "${PRODUCTNAME}"
VIAddVersionKey "LegalCopyright" "${COPYRIGHT}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

# additional plugins
!addplugindir "${ADDITIONALPLUGINSPATH}"

; Uninstaller signing command
!if "${UNINSTALLERSIGNCOMMAND}" != ""
  !uninstfinalize '${UNINSTALLERSIGNCOMMAND}'
!endif

; Handle install mode, `perUser`, `perMachine` or `both`
!if "${INSTALLMODE}" == "perMachine"
  RequestExecutionLevel admin
!endif

!if "${INSTALLMODE}" == "currentUser"
  RequestExecutionLevel user
!endif

!if "${INSTALLMODE}" == "both"
  !define MULTIUSER_MUI
  !define MULTIUSER_INSTALLMODE_INSTDIR "${PRODUCTNAME}"
  !define MULTIUSER_INSTALLMODE_COMMANDLINE
  !if "${ARCH}" == "x64"
    !define MULTIUSER_USE_PROGRAMFILES64
  !else if "${ARCH}" == "arm64"
    !define MULTIUSER_USE_PROGRAMFILES64
  !endif
  !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_KEY "${UNINSTKEY}"
  !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_VALUENAME "CurrentUser"
  !define MULTIUSER_INSTALLMODEPAGE_SHOWUSERNAME
  !define MULTIUSER_INSTALLMODE_FUNCTION RestorePreviousInstallLocation
  !define MULTIUSER_EXECUTIONLEVEL Highest
  !include MultiUser.nsh
!endif

; Installer icon
!if "${INSTALLERICON}" != ""
  !define MUI_ICON "${INSTALLERICON}"
!endif

; Installer sidebar image
!if "${SIDEBARIMAGE}" != ""
  !define MUI_WELCOMEFINISHPAGE_BITMAP "${SIDEBARIMAGE}"
!endif

; Enable header images for installer and uninstaller pages when either image is configured.
!if "${HEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE
!else if "${UNINSTALLERHEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE
!endif

; Installer header image
!if "${HEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE_BITMAP "${HEADERIMAGE}"
!endif

; Uninstaller header image
!if "${UNINSTALLERHEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE_UNBITMAP "${UNINSTALLERHEADERIMAGE}"
!endif

; Uninstaller icon
!if "${UNINSTALLERICON}" != ""
  !define MUI_UNICON "${UNINSTALLERICON}"
!endif

; Define registry key to store installer language
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "${MANUPRODUCTKEY}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "Installer Language"

; Installer pages, must be ordered as they appear
; 1. Welcome Page
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_WELCOME

; 2. License Page (if defined)
!if "${LICENSE}" != ""
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!endif

; 3. Install mode (if it is set to `both`)
!if "${INSTALLMODE}" == "both"
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !insertmacro MULTIUSER_PAGE_INSTALLMODE
!endif

; Previous application UninstallString values are never executed.
; Every destination must be current, an admitted predecessor, or missing.
; Forward update uses the pinned predecessor; repair accepts current or missing bytes.

; 5. Choose install directory page
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_DIRECTORY

; 6. Start menu shortcut page
Var AppStartMenuFolder
!if "${STARTMENUFOLDER}" != ""
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !define MUI_STARTMENUPAGE_DEFAULTFOLDER "${STARTMENUFOLDER}"
!else
  !define MUI_PAGE_CUSTOMFUNCTION_PRE Skip
!endif
!insertmacro MUI_PAGE_STARTMENU Application $AppStartMenuFolder

; 7. Installation page
!insertmacro MUI_PAGE_INSTFILES

; 8. Finish page
;
; Don't auto jump to finish page after installation page,
; because the installation page has useful info that can be used debug any issues with the installer.
!define MUI_FINISHPAGE_NOAUTOCLOSE
; Use show readme button in the finish page as a button create a desktop shortcut
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(createDesktop)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateOrUpdateDesktopShortcut
; Show run app after installation.
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_FINISH

Function RunMainBinary
  ${If} $MWINST_Completed != 1
    Return
  ${EndIf}
  !insertmacro MWOWN_CHECK "" "$INSTDIR" "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}" $0
  ${If} $0 != ${MWOWN_MATCHED}
    DetailPrint "Application launch refused: $MWOWN_LastReason"
    Return
  ${EndIf}
  ; CHECK closes its handle; this is not an atomic verified-process launch.
  nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" ""
FunctionEnd

; Uninstaller Pages
; 1. Confirm uninstall page
; Application data and project stores are always retained by this uninstaller.
!define MUI_PAGE_CUSTOMFUNCTION_PRE un.SkipIfPassive
!insertmacro MUI_UNPAGE_CONFIRM

; 2. Uninstalling Page
!insertmacro MUI_UNPAGE_INSTFILES

;Languages
{{#each languages}}
!insertmacro MUI_LANGUAGE "{{this}}"
{{/each}}
!insertmacro MUI_RESERVEFILE_LANGDLL
{{#each language_files}}
  !include "{{this}}"
{{/each}}

Function .onInit
  ${GetOptions} $CMDLINE "/P" $PassiveMode
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/NS" $NoShortcutMode
  ${IfNot} ${Errors}
    StrCpy $NoShortcutMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
  ${EndIf}

  !if "${DISPLAYLANGUAGESELECTOR}" == "true"
    !insertmacro MUI_LANGDLL_DISPLAY
  !endif

  !insertmacro SetContext

  ${If} $INSTDIR == "${PLACEHOLDER_INSTALL_DIR}"
    ; Set default install location
    !if "${INSTALLMODE}" == "perMachine"
      ${If} ${RunningX64}
        !if "${ARCH}" == "x64"
          StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
        !else if "${ARCH}" == "arm64"
          StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
        !else
          StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
        !endif
      ${Else}
        StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
      ${EndIf}
    !else if "${INSTALLMODE}" == "currentUser"
      StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
    !endif

    Call RestorePreviousInstallLocation
  ${EndIf}


  !if "${INSTALLMODE}" == "both"
    !insertmacro MULTIUSER_INIT
  !endif
FunctionEnd


Function MWINST_LookupCurrent
  StrCpy $MWINST_CurrentFound 0
  StrCpy $MWINST_CurrentSha ""
  StrCpy $MWINST_CurrentSize ""
  !insertmacro MWINST_CURRENT_ENTRY "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}"
  {{#each resources}}
    !insertmacro MWINST_CURRENT_ENTRY "{{this.[1]}}" "${MW_RESOURCE_{{@index}}_SHA256}" "${MW_RESOURCE_{{@index}}_SIZE}"
  {{/each}}
  {{#each binaries}}
    !insertmacro MWINST_CURRENT_ENTRY "{{this}}" "${MW_BINARY_{{@index}}_SHA256}" "${MW_BINARY_{{@index}}_SIZE}"
  {{/each}}
  !insertmacro MWINST_CURRENT_ENTRY "uninstall.exe" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize"
FunctionEnd

Function MWINST_Classify
  StrCpy $MWINST_Classification ""
  Call MWINST_LookupCurrent
  ${If} $MWINST_CurrentFound = 1
    !insertmacro MWOWN_CHECK "" "$INSTDIR" "$MWINST_Relative" "$MWINST_CurrentSha" "$MWINST_CurrentSize" $0
    ${If} $0 = ${MWOWN_MATCHED}
      StrCpy $MWINST_Classification "NEW"
      Return
    ${ElseIf} $0 = ${MWOWN_MISSING}
      StrCpy $MWINST_Classification "MISSING"
      Return
    ${EndIf}
    ; Only content mismatches may be compared with a predecessor. Path,
    ; access, identity, cleanup and read errors are never weakened.
    ${If} $MWOWN_LastReason != "hash_mismatch"
    ${AndIf} $MWOWN_LastReason != "size_mismatch"
      !insertmacro MWINST_FAIL "The destination could not be classified: $MWINST_Relative; $MWOWN_LastReason"
    ${EndIf}
  ${EndIf}
  !insertmacro MWPREV_LOOKUP "$MWINST_Relative"
  ${If} $MWINST_PrevFound != 1
    !insertmacro MWINST_FAIL "Destination bytes have no admitted predecessor: $MWINST_Relative"
  ${EndIf}
  !insertmacro MWOWN_CHECK "" "$INSTDIR" "$MWINST_Relative" "$MWINST_PrevSha" "$MWINST_PrevSize" $0
  ${If} $0 = ${MWOWN_MATCHED}
    StrCpy $MWINST_Classification "PREDECESSOR"
  ${ElseIf} $0 = ${MWOWN_MISSING}
    StrCpy $MWINST_Classification "MISSING"
  ${Else}
    !insertmacro MWINST_FAIL "Predecessor bytes changed or are unavailable: $MWINST_Relative; $MWOWN_LastReason"
  ${EndIf}
FunctionEnd

Function MWINST_DeletePrevious
  !insertmacro MWPREV_LOOKUP "$MWINST_Relative"
  ${If} $MWINST_PrevFound != 1
    !insertmacro MWINST_FAIL "No pinned predecessor authorizes this removal: $MWINST_Relative"
  ${EndIf}
  ; This is the only installer-side DELETE caller. Its expectation comes
  ; exclusively from the generated predecessor profile, never registry data.
  !insertmacro MWOWN_DELETE "" "$INSTDIR" "$MWINST_Relative" "$MWINST_PrevSha" "$MWINST_PrevSize" $0
  ${If} $0 = ${MWOWN_DELETE_UNCERTAIN}
    DetailPrint "Predecessor removal is uncertain; no automatic retry: $MWINST_Relative"
    SetErrorLevel 3
    Abort "Installation stopped with an uncertain file operation."
  ${ElseIf} $0 != ${MWOWN_DELETE_MARKED}
  ${AndIf} $0 != ${MWOWN_MISSING}
    !insertmacro MWINST_FAIL "The predecessor was preserved: $MWINST_Relative; $MWOWN_LastReason"
  ${EndIf}
FunctionEnd

Function MWINST_MakeDestinationAvailable
  Call MWINST_Classify
  StrCpy $MWINST_NeedFile 1
  ${If} $MWINST_Classification == "NEW"
    StrCpy $MWINST_NeedFile 0
  ${ElseIf} $MWINST_Classification == "PREDECESSOR"
    Call MWINST_DeletePrevious
  ${EndIf}
FunctionEnd

Function MWINST_PrepareSources
  SetOverwrite off
  !insertmacro MWINST_PREPARE_PAYLOAD MW_MAIN
  {{#each resources}}
    !insertmacro MWINST_PREPARE_PAYLOAD MW_RESOURCE_{{@index}}
  {{/each}}
  {{#each binaries}}
    !insertmacro MWINST_PREPARE_PAYLOAD MW_BINARY_{{@index}}
  {{/each}}
  ; Generate self only in the fresh private plugin directory. Observe this new
  ; source through the existing CNG guard, then publish without replacement.
  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "${MWOWN_NEW_SELF_NAME}" "0000000000000000000000000000000000000000000000000000000000000000" "0" $0
  ${If} $0 != ${MWOWN_MISSING}
    !insertmacro MWINST_FAIL "The new uninstaller staging name is not absent."
  ${EndIf}
  ClearErrors
  WriteUninstaller "$MWINST_PluginRoot\${MWOWN_NEW_SELF_NAME}"
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "The new uninstaller could not be generated."
  ${EndIf}
  !insertmacro MWOWN_OBSERVE_NEW_SELF "$MWINST_PluginRoot" $0
  ${If} $0 != ${MWOWN_OBSERVED_NEW_SELF}
    !insertmacro MWINST_FAIL "The new uninstaller source could not be observed; $MWOWN_LastReason"
  ${EndIf}
  StrCpy $MWINST_NewSelfSha $MWOWN_ActualSha
  StrCpy $MWINST_NewSelfSize $MWOWN_Size
  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "${MWOWN_NEW_SELF_NAME}" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
  ${If} $0 != ${MWOWN_MATCHED}
    !insertmacro MWINST_FAIL "The new uninstaller staging bytes changed; $MWOWN_LastReason"
  ${EndIf}
FunctionEnd

Function MWINST_Preflight
  ; Registry values can cause a refusal, but never grant file ownership or
  ; authorize an executable. A partial installation may have no registration.
  ReadRegStr $R0 SHCTX "${MANUPRODUCTKEY}" ""
  ${If} $R0 != ""
  ${AndIf} $R0 != $INSTDIR
    !insertmacro MWINST_FAIL "A registered installation uses another directory."
  ${EndIf}
  ReadRegStr $R0 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R0 != ""
  ${AndIf} $R0 != '$\"$INSTDIR\uninstall.exe$\"'
    !insertmacro MWINST_FAIL "The registered uninstaller does not name the selected directory."
  ${EndIf}
  ReadRegStr $R1 SHCTX "${UNINSTKEY}" "DisplayVersion"
  ${If} $R1 != ""
  ${AndIf} $R1 != "${VERSION}"
  ${AndIf} $R1 != "${MWPREV_VERSION}"
    !insertmacro MWINST_FAIL "This installed version has no admitted update profile."
  ${EndIf}
  ${If} $R1 != ""
    nsis_tauri_utils::SemverCompare "${VERSION}" $R1
    Pop $R0
    !if "${ALLOWDOWNGRADES}" != "true"
      ${If} $R0 = -1
        !insertmacro MWINST_FAIL "Downgrading is not enabled for this package."
      ${EndIf}
    !endif
    ${If} $R0 != -1
    ${AndIf} $R0 != 0
    ${AndIf} $R0 != 1
      !insertmacro MWINST_FAIL "The installed version could not be compared."
    ${EndIf}
  ${EndIf}

  ; Validate the prospective root through its existing parent. A content
  ; mismatch is classified against the full profile after root admission.
  ${GetParent} "$INSTDIR" $MWINST_Parent
  ${GetFileName} "$INSTDIR" $MWINST_Leaf
  !insertmacro MWOWN_CHECK "" "$MWINST_Parent" "$MWINST_Leaf\uninstall.exe" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
  ${If} $0 != ${MWOWN_MISSING}
  ${AndIf} $0 != ${MWOWN_MATCHED}
    ${If} $MWOWN_LastReason != "hash_mismatch"
    ${AndIf} $MWOWN_LastReason != "size_mismatch"
      !insertmacro MWINST_FAIL "The destination root was not admitted; $MWOWN_LastReason"
    ${EndIf}
  ${EndIf}
  ClearErrors
  CreateDirectory "$INSTDIR"
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "The destination directory could not be created."
  ${EndIf}
  StrCpy $MWINST_Relative "${MAINBINARYNAME}.exe"
  Call MWINST_Classify
  {{#each resources}}
    StrCpy $MWINST_Relative "{{this.[1]}}"
    Call MWINST_Classify
  {{/each}}
  {{#each binaries}}
    StrCpy $MWINST_Relative "{{this}}"
    Call MWINST_Classify
  {{/each}}
  StrCpy $MWINST_Relative "uninstall.exe"
  Call MWINST_Classify
  ; Include obsolete names. A changed old resource vetoes the whole update
  ; before any payload or self removal, even if the new package omitted it.
  !insertmacro MWPREV_FOR_EACH MWINST_PREFLIGHT_PREVIOUS
FunctionEnd

Section EarlyChecks
  InitPluginsDir
  ; Only the fresh NSIS scratch directory is converted from a possible 8.3
  ; spelling. User-selected target paths keep the guard's alias rejection.
  StrCpy $0 "$PLUGINSDIR"
  StrCpy $1 ""
  System::Call 'kernel32::GetLongPathNameW(w r0, w .r1, i ${NSIS_MAX_STRLEN}) i.r2'
  ${If} $2 <= 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
    !insertmacro MWINST_FAIL "The private installer staging path could not be resolved."
  ${EndIf}
  StrCpy $MWINST_PluginRoot $1
  Call MWINST_PrepareSources
  Call MWINST_Preflight
SectionEnd

Section WebView2
  SetOverwrite off
  ; Check if Webview2 is already installed and skip this section
  ${If} ${RunningX64}
    ReadRegStr $4 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${Else}
    ReadRegStr $4 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${EndIf}
  ${If} $4 == ""
    ReadRegStr $4 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${EndIf}

  ${If} $4 == ""
    ; Webview2 installation
    ;
    ; Skip if updating
    ${If} $UpdateMode <> 1
      !if "${INSTALLWEBVIEW2MODE}" == "downloadBootstrapper"
        DetailPrint "$(webview2Downloading)"
        NSISdl::download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" "$MWINST_PluginRoot\MicrosoftEdgeWebview2Setup.exe"
        Pop $0
        ${If} $0 == "success"
          DetailPrint "$(webview2DownloadSuccess)"
        ${Else}
          DetailPrint "$(webview2DownloadError)"
          Abort "$(webview2AbortError)"
        ${EndIf}
        StrCpy $6 "$MWINST_PluginRoot\MicrosoftEdgeWebview2Setup.exe"
        Goto install_webview2
      !endif

      !if "${INSTALLWEBVIEW2MODE}" == "embedBootstrapper"
        ClearErrors
        File "/oname=$MWINST_PluginRoot\MicrosoftEdgeWebview2Setup.exe" "${WEBVIEW2BOOTSTRAPPERPATH}"
        ${If} ${Errors}
          !insertmacro MWINST_FAIL "WebView bootstrapper staging failed."
        ${EndIf}
        DetailPrint "$(installingWebview2)"
        StrCpy $6 "$MWINST_PluginRoot\MicrosoftEdgeWebview2Setup.exe"
        Goto install_webview2
      !endif

      !if "${INSTALLWEBVIEW2MODE}" == "offlineInstaller"
        ClearErrors
        File "/oname=$MWINST_PluginRoot\MicrosoftEdgeWebView2RuntimeInstaller.exe" "${WEBVIEW2INSTALLERPATH}"
        ${If} ${Errors}
          !insertmacro MWINST_FAIL "WebView runtime installer staging failed."
        ${EndIf}
        DetailPrint "$(installingWebview2)"
        StrCpy $6 "$MWINST_PluginRoot\MicrosoftEdgeWebView2RuntimeInstaller.exe"
        Goto install_webview2
      !endif

      Goto webview2_done

      install_webview2:
        DetailPrint "$(installingWebview2)"
        ; $6 holds the path to the webview2 installer
        ClearErrors
        StrCpy $1 "NOT_STARTED"
        ExecWait '"$6" ${WEBVIEW2INSTALLERARGS} /install' $1
        ${If} ${Errors}
          !insertmacro MWINST_FAIL "WebView installer launch failed."
        ${EndIf}
        ${If} $1 = 0
          DetailPrint "$(webview2InstallSuccess)"
        ${Else}
          DetailPrint "$(webview2InstallError)"
          Abort "$(webview2AbortError)"
        ${EndIf}
      webview2_done:
    ${EndIf}
  ${Else}
    !if "${MINIMUMWEBVIEW2VERSION}" != ""
      ${VersionCompare} "${MINIMUMWEBVIEW2VERSION}" "$4" $R0
      ${If} $R0 = 1
        update_webview:
          DetailPrint "$(installingWebview2)"
          ${If} ${RunningX64}
            ReadRegStr $R1 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate" "path"
          ${Else}
            ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\EdgeUpdate" "path"
          ${EndIf}
          ${If} $R1 == ""
            ReadRegStr $R1 HKCU "SOFTWARE\Microsoft\EdgeUpdate" "path"
          ${EndIf}
          ${If} $R1 != ""
            ; Chromium updater docs: https://source.chromium.org/chromium/chromium/src/+/main:docs/updater/user_manual.md
            ; Modified from "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft EdgeWebView\ModifyPath"
            ExecWait `"$R1" /install appguid=${WEBVIEW2APPGUID}&needsadmin=true` $1
            ${If} $1 = 0
              DetailPrint "$(webview2InstallSuccess)"
            ${Else}
              MessageBox MB_ICONEXCLAMATION|MB_ABORTRETRYIGNORE "$(webview2InstallError)" IDIGNORE ignore IDRETRY update_webview
              Quit
              ignore:
            ${EndIf}
          ${EndIf}
      ${EndIf}
    !endif
  ${EndIf}
SectionEnd

Section Install
  SetOverwrite off
  ClearErrors
  SetOutPath $INSTDIR
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "The payload output directory could not be selected."
  ${EndIf}

  !ifmacrodef NSIS_HOOK_PREINSTALL
    !insertmacro NSIS_HOOK_PREINSTALL
  !endif

  !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

  ; Revalidate each source and destination. Matched current files are retained;
  ; only a complete pinned predecessor permits removing different bytes.
  !insertmacro MWINST_INSTALL_PAYLOAD "${MAINBINARYNAME}.exe" MW_MAIN

  {{#each resources_dirs}}
    ClearErrors
    CreateDirectory "$INSTDIR\{{this}}"
    ${If} ${Errors}
      !insertmacro MWINST_FAIL "A resource directory could not be created."
    ${EndIf}
  {{/each}}
  {{#each resources}}
    !insertmacro MWINST_INSTALL_PAYLOAD "{{this.[1]}}" MW_RESOURCE_{{@index}}
  {{/each}}
  {{#each binaries}}
    !insertmacro MWINST_INSTALL_PAYLOAD "{{this}}" MW_BINARY_{{@index}}
  {{/each}}

  ; Obsolete payloads also use the same hash-and-delete handle. No runtime
  ; OldMainBinaryName lookup supplies additional paths.
  !insertmacro MWPREV_FOR_EACH MWINST_REMOVE_OBSOLETE

  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "${MWOWN_NEW_SELF_NAME}" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
  ${If} $0 != ${MWOWN_MATCHED}
    !insertmacro MWINST_FAIL "The prepared uninstaller changed; $MWOWN_LastReason"
  ${EndIf}
  StrCpy $MWINST_Relative "uninstall.exe"
  Call MWINST_Classify
  ${If} $MWINST_Classification != "NEW"
    ; WriteUninstaller cannot be embedded in File. Copy its fully checked
    ; private source to a same-volume stage, preserving the old executable
    ; until that staged copy is byte-verified.
    StrCpy $MWINST_StageLeaf "mw-uninstaller-stage-$MWINST_NewSelfSha.bin"
    !insertmacro MWOWN_CHECK "" "$INSTDIR" "$MWINST_StageLeaf" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
    ${If} $0 = ${MWOWN_MISSING}
      ClearErrors
      ; Fail if a separate writer creates the stage after the absence check.
      ; CopyFiles may replace it; Win32 CopyFileW(...,TRUE) never does.
      StrCpy $2 "$MWINST_PluginRoot\${MWOWN_NEW_SELF_NAME}"
      StrCpy $3 "$INSTDIR\$MWINST_StageLeaf"
      System::Call 'kernel32::CopyFileW(w r2, w r3, i 1) i.r0 ?e'
      Pop $1
      ${If} $0 = 0
        !insertmacro MWINST_FAIL "The uninstaller staging copy was refused; previous bytes retained."
      ${EndIf}
    ${ElseIf} $0 != ${MWOWN_MATCHED}
      !insertmacro MWINST_FAIL "Untrusted uninstaller staging file retained; $MWOWN_LastReason"
    ${EndIf}
    !insertmacro MWOWN_CHECK "" "$INSTDIR" "$MWINST_StageLeaf" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
    ${If} $0 != ${MWOWN_MATCHED}
      !insertmacro MWINST_FAIL "The fully staged uninstaller was not verified; $MWOWN_LastReason"
    ${EndIf}
    StrCpy $MWINST_Relative "uninstall.exe"
    Call MWINST_MakeDestinationAvailable
    ${If} $MWINST_NeedFile = 1
      !insertmacro MWINST_REQUIRE_ABSENT "uninstall.exe" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize"
      !insertmacro MWOWN_PUBLISH "$INSTDIR" "$MWINST_StageLeaf" "uninstall.exe" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize" $0
      ${If} $0 = ${MWOWN_PUBLISH_UNCERTAIN}
        SetErrorLevel 3
        Abort "Uninstaller publication uncertain; registration retained and no retry attempted."
      ${ElseIf} $0 != ${MWOWN_PUBLISHED}
        !insertmacro MWINST_FAIL "Uninstaller held-handle publication was refused; $MWOWN_LastReason"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  !insertmacro MWINST_REQUIRE_MATCHED "uninstall.exe" "$MWINST_NewSelfSha" "$MWINST_NewSelfSize"

  ClearErrors
  ; Create file associations
  {{#each file_associations as |association| ~}}
    {{#each association.ext as |ext| ~}}
       !insertmacro APP_ASSOCIATE "{{ext}}" "{{or association.name ext}}" "{{association-description association.description ext}}" "$INSTDIR\${MAINBINARYNAME}.exe,0" "Open with ${PRODUCTNAME}" "$INSTDIR\${MAINBINARYNAME}.exe $\"%1$\""
    {{/each}}
  {{/each}}

  ; Register deep links
  {{#each deep_link_protocols as |protocol| ~}}
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "URL Protocol" ""
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "" "URL:${BUNDLEID} protocol"
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}\DefaultIcon" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\",0"
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}\shell\open\command" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
  {{/each}}

  ; Save $INSTDIR in registry for future installations
  WriteRegStr SHCTX "${MANUPRODUCTKEY}" "" $INSTDIR

  !if "${INSTALLMODE}" == "both"
    ; Save install mode to be selected by default for the next installation such as updating
    ; or when uninstalling
    WriteRegStr SHCTX "${UNINSTKEY}" $MultiUser.InstallMode 1
  !endif

  ; Registry MainBinaryName is not byte authority. Only generated predecessor
  ; entries can authorize removal of a former executable name.

  ; Save current MAINBINARYNAME for future updates
  WriteRegStr SHCTX "${UNINSTKEY}" "MainBinaryName" "${MAINBINARYNAME}.exe"

  ; Registry information for add/remove programs
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "${PRODUCTNAME}"
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayIcon" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINSTKEY}" "Publisher" "${MANUFACTURER}"
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$\"$INSTDIR$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoModify" "1"
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoRepair" "1"

  ; Check the registry group before GetSize or shortcut helpers can change
  ; the shared NSIS error flag. Partial registration is not success.
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "Payloads are present but registration did not complete."
  ${EndIf}
  ${GetSize} "$INSTDIR" "/M=uninstall.exe /S=0K /G=0" $0 $1 $2
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "The installed size could not be recorded."
  ${EndIf}
  IntOp $0 $0 + ${ESTIMATEDSIZE}
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD SHCTX "${UNINSTKEY}" "EstimatedSize" "$0"

  !if "${HOMEPAGE}" != ""
    WriteRegStr SHCTX "${UNINSTKEY}" "URLInfoAbout" "${HOMEPAGE}"
    WriteRegStr SHCTX "${UNINSTKEY}" "URLUpdateInfo" "${HOMEPAGE}"
    WriteRegStr SHCTX "${UNINSTKEY}" "HelpLink" "${HOMEPAGE}"
  !endif

  ${If} ${Errors}
    !insertmacro MWINST_FAIL "Payloads are present but registration did not complete."
  ${EndIf}

  ; Create start menu shortcut
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    Call CreateOrUpdateStartMenuShortcut
  !insertmacro MUI_STARTMENU_WRITE_END
  ${If} ${Errors}
    !insertmacro MWINST_FAIL "Start menu registration did not complete."
  ${EndIf}

  ; Create desktop shortcut for silent and passive installers
  ; because finish page will be skipped
  ${If} $PassiveMode = 1
  ${OrIf} ${Silent}
    Call CreateOrUpdateDesktopShortcut
  ${EndIf}

  !ifmacrodef NSIS_HOOK_POSTINSTALL
    !insertmacro NSIS_HOOK_POSTINSTALL
  !endif

  ${If} ${Errors}
    !insertmacro MWINST_FAIL "The final installation actions did not complete."
  ${EndIf}
  StrCpy $MWINST_Completed 1

  ; Auto close this page for passive mode
  ${If} $PassiveMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd

Function .onInstSuccess
  ; Check for `/R` flag only in silent and passive installers because
  ; GUI installer has a toggle for the user to (re)start the app
  ${If} $PassiveMode = 1
  ${OrIf} ${Silent}
    ${GetOptions} $CMDLINE "/R" $R0
    ${IfNot} ${Errors}
      ${GetOptions} $CMDLINE "/ARGS" $R0
      StrCpy $R1 $R0
      !insertmacro MWOWN_CHECK "" "$INSTDIR" "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}" $0
      ${If} $MWINST_Completed = 1
      ${AndIf} $0 = ${MWOWN_MATCHED}
        ; CHECK is an observation, not a held-handle launch.
        nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" "$R1"
      ${Else}
        DetailPrint "Application launch refused: $MWOWN_LastReason"
      ${EndIf}
    ${EndIf}
  ${EndIf}
FunctionEnd

Function un.onInit
  !insertmacro SetContext

  !if "${INSTALLMODE}" == "both"
    !insertmacro MULTIUSER_UNINIT
  !endif

  !insertmacro MUI_UNGETLANGUAGE

  ${GetOptions} $CMDLINE "/P" $PassiveMode
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
  ${IfNot} ${Errors}
    SetErrorLevel 2
    Abort "In-place update is not supported by this uninstaller."
  ${EndIf}
FunctionEnd

Section Uninstall

  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !endif

  StrCpy $MWINST_UninstallPreserved 0
  StrCpy $MWINST_UninstallUncertain 0

  ; Preflight all listed payloads before any deletion. DELETE repeats the byte
  ; and path checks on the exact handle used for disposition.
  !insertmacro MWINST_UNINSTALL_PREFLIGHT "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}"
  {{#each resources}}
    !insertmacro MWINST_UNINSTALL_PREFLIGHT "{{this.[1]}}" "${MW_RESOURCE_{{@index}}_SHA256}" "${MW_RESOURCE_{{@index}}_SIZE}"
  {{/each}}
  {{#each binaries}}
    !insertmacro MWINST_UNINSTALL_PREFLIGHT "{{this}}" "${MW_BINARY_{{@index}}_SHA256}" "${MW_BINARY_{{@index}}_SIZE}"
  {{/each}}
  ${If} $MWINST_UninstallPreserved = 1
    SetErrorLevel 2
    Abort "Uninstall refused: a payload could not be verified. Files and registration were retained."
  ${EndIf}

  !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

  !insertmacro MWINST_DELETE_OWNED "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}"
  {{#each resources}}
    !insertmacro MWINST_DELETE_OWNED "{{this.[1]}}" "${MW_RESOURCE_{{@index}}_SHA256}" "${MW_RESOURCE_{{@index}}_SIZE}"
  {{/each}}
  {{#each binaries}}
    !insertmacro MWINST_DELETE_OWNED "{{this}}" "${MW_BINARY_{{@index}}_SHA256}" "${MW_BINARY_{{@index}}_SIZE}"
  {{/each}}
  ${If} $MWINST_UninstallUncertain = 1
    SetErrorLevel 3
    Abort "Uninstall outcome is uncertain. No deletion was retried; registration was retained."
  ${EndIf}
  ${If} $MWINST_UninstallPreserved = 1
    SetErrorLevel 2
    Abort "Uninstall stopped after a preservation decision. Registration was retained."
  ${EndIf}

  ; Delete app associations
  {{#each file_associations as |association| ~}}
    {{#each association.ext as |ext| ~}}
      !insertmacro APP_UNASSOCIATE "{{ext}}" "{{or association.name ext}}"
    {{/each}}
  {{/each}}

  ; Delete deep links
  {{#each deep_link_protocols as |protocol| ~}}
    ReadRegStr $R7 SHCTX "Software\Classes\\{{protocol}}\shell\open\command" ""
    ${If} $R7 == "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
      DeleteRegKey SHCTX "Software\Classes\\{{protocol}}"
    ${EndIf}
  {{/each}}


  ; Self and shortcuts are retained: this package does not carry independently
  ; authenticated byte baselines for their later removal. A shortcut target or
  ; registry value cannot substitute for those baselines.
  {{#each resources_ancestors}}
    RMDir "$INSTDIR\{{this}}"
  {{/each}}
  ; The retained uninstall.exe keeps the install root. No deferred removal is queued.

  ; Remove registry information for add/remove programs
  !if "${INSTALLMODE}" == "both"
    DeleteRegKey SHCTX "${UNINSTKEY}"
  !else if "${INSTALLMODE}" == "perMachine"
    DeleteRegKey HKLM "${UNINSTKEY}"
  !else
    DeleteRegKey HKCU "${UNINSTKEY}"
  !endif

  ; Removes the Autostart entry for ${PRODUCTNAME} from the HKCU Run key if it exists.
  ; This ensures the program does not launch automatically after uninstallation if it exists.
  ; If it doesn't exist, it does nothing.
  ; We do this when not updating (to preserve the registry value on updates)
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
  ${EndIf}

  ; Project stores and application data are never recursively removed here.
  ; The install-location and language metadata are retained for inspection.

  !ifmacrodef NSIS_HOOK_POSTUNINSTALL
    !insertmacro NSIS_HOOK_POSTUNINSTALL
  !endif

  ; Auto close if passive mode or updating
  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd

Function RestorePreviousInstallLocation
  ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
  StrCmp $4 "" +2 0
    StrCpy $INSTDIR $4
FunctionEnd

Function Skip
  Abort
FunctionEnd

Function SkipIfPassive
  ${IfThen} $PassiveMode = 1  ${|} Abort ${|}
FunctionEnd
Function un.SkipIfPassive
  ${IfThen} $PassiveMode = 1  ${|} Abort ${|}
FunctionEnd

Function MWINST_PublishShortcut
  ; Existing links have no embedded byte baseline and are never rewritten.
  !insertmacro MWOWN_CHECK "" "$INSTDIR" "${MAINBINARYNAME}.exe" "${MW_MAIN_SHA256}" "${MW_MAIN_SIZE}" $0
  ${If} $0 != ${MWOWN_MATCHED}
    DetailPrint "Shortcut creation skipped: executable bytes were not verified."
    Return
  ${EndIf}
  !insertmacro MWOWN_CHECK "" "$MWINST_ShortcutRoot" "$MWINST_ShortcutRelative" "0000000000000000000000000000000000000000000000000000000000000000" "0" $0
  ${If} $0 != ${MWOWN_MISSING}
    DetailPrint "Shortcut preserved or destination refused: $MWOWN_LastReason"
    Return
  ${EndIf}
  StrCpy $MWINST_ShortcutTarget "$MWINST_ShortcutRoot\$MWINST_ShortcutRelative"
  ${GetParent} "$MWINST_ShortcutTarget" $MWINST_Parent
  ClearErrors
  CreateDirectory "$MWINST_Parent"
  ${If} ${Errors}
    DetailPrint "Shortcut creation skipped: destination directory could not be created."
    Return
  ${EndIf}
  !insertmacro MWOWN_CHECK "" "$MWINST_PluginRoot" "$MWINST_ShortcutSource" "0000000000000000000000000000000000000000000000000000000000000000" "0" $0
  ${If} $0 != ${MWOWN_MISSING}
    DetailPrint "Shortcut creation skipped: staging name is not absent."
    Return
  ${EndIf}
  ClearErrors
  CreateShortcut "$MWINST_PluginRoot\$MWINST_ShortcutSource" "$INSTDIR\${MAINBINARYNAME}.exe"
  ${If} ${Errors}
    DetailPrint "Shortcut creation failed."
    Return
  ${EndIf}
  !insertmacro SetLnkAppUserModelId "$MWINST_PluginRoot\$MWINST_ShortcutSource"
  !insertmacro MWOWN_CHECK "" "$MWINST_ShortcutRoot" "$MWINST_ShortcutRelative" "0000000000000000000000000000000000000000000000000000000000000000" "0" $0
  ${If} $0 != ${MWOWN_MISSING}
    DetailPrint "Shortcut destination changed and was retained: $MWOWN_LastReason"
    Return
  ${EndIf}
  ClearErrors
  ; No /REBOOTOK and no fallback: an existing destination must survive.
  Rename "$MWINST_PluginRoot\$MWINST_ShortcutSource" "$MWINST_ShortcutTarget"
  ${If} ${Errors}
    DetailPrint "Shortcut publication failed; no destination was replaced."
  ${EndIf}
FunctionEnd

Function CreateOrUpdateStartMenuShortcut
  ${If} $UpdateMode = 1
  ${OrIf} $NoShortcutMode = 1
    Return
  ${EndIf}
  StrCpy $MWINST_ShortcutRoot "$SMPROGRAMS"
  !if "${STARTMENUFOLDER}" != ""
    StrCpy $MWINST_ShortcutRelative "$AppStartMenuFolder\${PRODUCTNAME}.lnk"
  !else
    StrCpy $MWINST_ShortcutRelative "${PRODUCTNAME}.lnk"
  !endif
  StrCpy $MWINST_ShortcutSource "mw-new-start-menu.lnk"
  Call MWINST_PublishShortcut
FunctionEnd

Function CreateOrUpdateDesktopShortcut
  ${If} $UpdateMode = 1
  ${OrIf} $NoShortcutMode = 1
    Return
  ${EndIf}
  StrCpy $MWINST_ShortcutRoot "$DESKTOP"
  StrCpy $MWINST_ShortcutRelative "${PRODUCTNAME}.lnk"
  StrCpy $MWINST_ShortcutSource "mw-new-desktop.lnk"
  Call MWINST_PublishShortcut
FunctionEnd
