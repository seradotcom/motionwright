; File ownership guard for the pinned Unicode NSIS Windows installer.
; SHA256 and size must describe the exact private capture passed to File.
; Delete authority is never derived from the install directory, registry, or app data.
; OBSERVE_NEW_SELF only hashes the freshly generated uninstaller in $PLUGINSDIR;
; its observation is used to verify exclusive publication, never to authorize Delete.
;
; Primary references:
; https://github.com/nsis-dev/nsis/blob/7359413009afd4f0fff472d841fc2f2cc0e0a5f8/Contrib/System/System.html
; https://learn.microsoft.com/windows/win32/api/bcrypt/nf-bcrypt-bcryptcreatehash
; https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle
;
; Public macros:
; !insertmacro MWOWN_CHECK "" "$INSTDIR" "app.exe" "${SHA}" "${SIZE}" $0
; !insertmacro MWOWN_DELETE "un." "$INSTDIR" "app.exe" "${SHA}" "${SIZE}" $0
;
; Prefix is "" for installer or "un." for uninstaller. MWOWN_LastReason contains
; a static reason. CHECK changes no disposition. DELETE marks the verified handle
; for deletion, then closes it. Other readers can defer namespace disappearance.
; Stack bottom to top: root, relative, sha256, size, mode (0/1/2).
; Five inputs are consumed and one result is returned. Modes 0/1 are CHECK/DELETE;
; mode 2 is installer-only observation of the fixed new-self staging name.
; $0-$5 and the NSIS error flag are preserved. Other ordinary registers,
; $INSTDIR and $OUTDIR are untouched.
; MWOWN_* globals are private scratch state. Calls must not be reentered.
;
; Only ordinary local-drive directories and bounded relative paths are supported.
; Observed reparse points, volume roots, device/UNC syntax, non-disk handles and
; ambiguous normalized paths are preserved. Reads use one 64 KiB buffer and are
; bounded by the embedded size. Synchronous I/O has no hard wall-clock deadline.
; The uninstaller retains uninstall.exe: a full-file digest cannot be embedded
; in that same file. Replacing a previous uninstaller requires its independently
; obtained digest embedded in the new installer.
;
!ifndef MOTIONWRIGHT_UNINSTALL_OWNERSHIP_NSH
!define MOTIONWRIGHT_UNINSTALL_OWNERSHIP_NSH
!include "LogicLib.nsh"
!ifndef NSIS_UNICODE
!error "Motionwright ownership guard requires Unicode NSIS."
!endif
!define MWOWN_MATCHED 0
!define MWOWN_DELETE_MARKED 1
!define MWOWN_MISSING 2
!define MWOWN_PRESERVED 3
!define MWOWN_DELETE_UNCERTAIN 4
!define MWOWN_OBSERVED_NEW_SELF 5
!define MWOWN_NEW_SELF_NAME "mw-owned-new-uninstaller.exe"
; Tauri canonicalizes installerHooks before including this file. The helper stays
; in the same reviewed source checkout; no installed destination supplies tooling.
!define MWOWN_BUILD_HELPER "${__FILEDIR__}\..\..\..\..\tooling\nsis_owned_files.py"
!define MWOWN_MAX_FILE_BYTES 2147483647

Var MWOWN_LastReason
Var MWOWN_Root
Var MWOWN_Relative
Var MWOWN_ExpectedSha
Var MWOWN_Size
Var MWOWN_Mode
Var MWOWN_Result
Var MWOWN_SavedError
Var MWOWN_RootHandle
Var MWOWN_FileHandle
Var MWOWN_Algorithm
Var MWOWN_Hash
Var MWOWN_Buffer
Var MWOWN_Digest
Var MWOWN_Info
Var MWOWN_PathBuffer
Var MWOWN_DeleteInfo
Var MWOWN_RootFull
Var MWOWN_RootPhysical
Var MWOWN_Path
Var MWOWN_ExpectedPhysical
Var MWOWN_Prefix
Var MWOWN_Component
Var MWOWN_Index
Var MWOWN_Length
Var MWOWN_Remaining
Var MWOWN_Want
Var MWOWN_Got
Var MWOWN_ActualSha
Var MWOWN_HexPointer

!macro MWOWN_CHECK Prefix Root Relative Sha Size Result
  Push "${Root}"
  Push "${Relative}"
  Push "${Sha}"
  Push "${Size}"
  Push "0"
  Call ${Prefix}MWOWN_Guard
  Pop ${Result}
!macroend

!macro MWOWN_DELETE Prefix Root Relative Sha Size Result
  Push "${Root}"
  Push "${Relative}"
  Push "${Sha}"
  Push "${Size}"
  Push "1"
  Call ${Prefix}MWOWN_Guard
  Pop ${Result}
!macroend

!macro MWOWN_OBSERVE_NEW_SELF Root Result
  Push "${Root}"
  Push "${MWOWN_NEW_SELF_NAME}"
  Push "0000000000000000000000000000000000000000000000000000000000000000"
  Push "0"
  Push "2"
  Call MWOWN_Guard
  Pop ${Result}
!macroend

; Private macros are expanded only inside a guard worker.
!macro MWOWN_REQUIRE_BOOL Reason
  ${If} $0 = 0
    StrCpy $MWOWN_LastReason "${Reason}"
    Goto mwown_exit
  ${EndIf}
!macroend

!macro MWOWN_REQUIRE_STATUS Reason
  ${If} $0 != "0"
    StrCpy $MWOWN_LastReason "${Reason}"
    Goto mwown_exit
  ${EndIf}
!macroend

!macro MWOWN_VALIDATE_COMPONENT
  ${If} $MWOWN_Component == ""
    StrCpy $MWOWN_LastReason "empty_path_component"
    Goto mwown_exit
  ${EndIf}
  StrCpy $0 $MWOWN_Component 1 -1
  ${If} $0 == "."
  ${OrIf} $0 == " "
    StrCpy $MWOWN_LastReason "ambiguous_path_component"
    Goto mwown_exit
  ${EndIf}
!macroend

!macro MWOWN_DEFINE_WORKER Prefix
Function ${Prefix}MWOWN_Guard
  Pop $MWOWN_Mode
  Pop $MWOWN_Size
  Pop $MWOWN_ExpectedSha
  Pop $MWOWN_Relative
  Pop $MWOWN_Root
  Push $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  StrCpy $MWOWN_SavedError 0
  IfErrors 0 mwown_error_saved
  StrCpy $MWOWN_SavedError 1
mwown_error_saved:
  StrCpy $MWOWN_Result ${MWOWN_PRESERVED}
  StrCpy $MWOWN_LastReason "invalid_arguments"
  StrCpy $MWOWN_RootHandle 0
  StrCpy $MWOWN_FileHandle 0
  StrCpy $MWOWN_Algorithm 0
  StrCpy $MWOWN_Hash 0
  StrCpy $MWOWN_Buffer 0
  StrCpy $MWOWN_Digest 0
  StrCpy $MWOWN_Info 0
  StrCpy $MWOWN_PathBuffer 0
  StrCpy $MWOWN_DeleteInfo 0

  ${If} $MWOWN_Mode != "0"
  ${AndIf} $MWOWN_Mode != "1"
  ${AndIf} $MWOWN_Mode != "2"
    Goto mwown_exit
  ${EndIf}
  ${If} $MWOWN_Mode == "2"
!if "${Prefix}" == "un."
    StrCpy $MWOWN_LastReason "observation_not_admitted_in_uninstaller"
    Goto mwown_exit
!else
    ; NSIS may expose its newly created temporary directory through an 8.3
    ; spelling. Admit only its long form, never a caller-selected source root.
    StrCpy $0 "$PLUGINSDIR"
    StrCpy $1 ""
    System::Call 'kernel32::GetLongPathNameW(w r0, w .r1, i ${NSIS_MAX_STRLEN}) i.r2'
    ${If} $2 <= 0
    ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
      StrCpy $MWOWN_LastReason "new_self_staging_unavailable"
      Goto mwown_exit
    ${EndIf}
    ${If} $MWOWN_Root != $1
    ${OrIf} $MWOWN_Relative != "${MWOWN_NEW_SELF_NAME}"
      StrCpy $MWOWN_LastReason "observation_requires_new_self_staging"
      Goto mwown_exit
    ${EndIf}
!endif
  ${EndIf}
!if "${Prefix}" == "un."
  ${If} $MWOWN_Mode == "1"
  ${AndIf} $MWOWN_Relative == "uninstall.exe"
    StrCpy $MWOWN_LastReason "self_file_retained"
    Goto mwown_exit
  ${EndIf}
!endif

  StrLen $0 $MWOWN_ExpectedSha
  ${If} $0 != 64
    Goto mwown_exit
  ${EndIf}
  ; Canonical decimal, at most 2 GiB - 1; no signed counter overflow.
  StrLen $MWOWN_Length $MWOWN_Size
  ${If} $MWOWN_Length < 1
  ${OrIf} $MWOWN_Length > 10
    Goto mwown_exit
  ${EndIf}
  StrCpy $0 $MWOWN_Size 1
  ${If} $0 == "0"
  ${AndIf} $MWOWN_Length > 1
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_Index 0
mwown_size_digit:
  StrCpy $0 $MWOWN_Size 1 $MWOWN_Index
  StrCmp $0 "0" mwown_size_digit_ok
  StrCmp $0 "1" mwown_size_digit_ok
  StrCmp $0 "2" mwown_size_digit_ok
  StrCmp $0 "3" mwown_size_digit_ok
  StrCmp $0 "4" mwown_size_digit_ok
  StrCmp $0 "5" mwown_size_digit_ok
  StrCmp $0 "6" mwown_size_digit_ok
  StrCmp $0 "7" mwown_size_digit_ok
  StrCmp $0 "8" mwown_size_digit_ok
  StrCmp $0 "9" mwown_size_digit_ok
  Goto mwown_exit
mwown_size_digit_ok:
  IntOp $MWOWN_Index $MWOWN_Index + 1
  ${If} $MWOWN_Index < $MWOWN_Length
    Goto mwown_size_digit
  ${EndIf}
  System::Int64Op $MWOWN_Size ">" ${MWOWN_MAX_FILE_BYTES}
  Pop $0
  ${If} $0 != "0"
    StrCpy $MWOWN_LastReason "size_limit"
    Goto mwown_exit
  ${EndIf}
  ; Bound concatenations before native calls.
  StrLen $0 $MWOWN_Root
  StrLen $1 $MWOWN_Relative
  ${If} $0 < 4
  ${OrIf} $1 < 1
    Goto mwown_exit
  ${EndIf}
  IntOp $2 $0 + $1
  IntOp $2 $2 + 8
  ${If} $2 >= ${NSIS_MAX_STRLEN}
    StrCpy $MWOWN_LastReason "path_limit"
    Goto mwown_exit
  ${EndIf}
  StrCpy $0 $MWOWN_Root 1 1
  StrCpy $1 $MWOWN_Root 1 2
  ${If} $0 != ":"
  ${OrIf} $1 != "\"
    StrCpy $MWOWN_LastReason "unsupported_root"
    Goto mwown_exit
  ${EndIf}

  ; Validate every component before testing whether any directory is absent.
  StrLen $MWOWN_Length $MWOWN_Relative
  StrCpy $MWOWN_Index 0
  StrCpy $MWOWN_Component ""
mwown_relative_char:
  StrCpy $0 $MWOWN_Relative 1 $MWOWN_Index
  ${If} $0 == "\"
    !insertmacro MWOWN_VALIDATE_COMPONENT
    StrCpy $MWOWN_Component ""
  ${Else}
    ${If} $0 == "/"
    ${OrIf} $0 == ":"
    ${OrIf} $0 == "*"
    ${OrIf} $0 == "?"
    ${OrIf} $0 == "<"
    ${OrIf} $0 == ">"
    ${OrIf} $0 == "|"
    ${OrIf} $0 == '"'
    ${OrIf} $0 == "$\r"
    ${OrIf} $0 == "$\n"
    ${OrIf} $0 == "$\t"
      StrCpy $MWOWN_LastReason "invalid_path_character"
      Goto mwown_exit
    ${EndIf}
    StrCpy $MWOWN_Component "$MWOWN_Component$0"
  ${EndIf}
  IntOp $MWOWN_Index $MWOWN_Index + 1
  ${If} $MWOWN_Index < $MWOWN_Length
    Goto mwown_relative_char
  ${EndIf}
  !insertmacro MWOWN_VALIDATE_COMPONENT

  IntOp $0 ${NSIS_MAX_STRLEN} * 2
  System::Alloc $0
  Pop $MWOWN_PathBuffer
  ${If} $MWOWN_PathBuffer = 0
    StrCpy $MWOWN_LastReason "allocation_failed"
    Goto mwown_exit
  ${EndIf}
  System::Alloc 52
  Pop $MWOWN_Info
  ${If} $MWOWN_Info = 0
    StrCpy $MWOWN_LastReason "allocation_failed"
    Goto mwown_exit
  ${EndIf}
  StrCpy $2 $MWOWN_Root
  System::Call 'kernel32::GetFullPathNameW(w r2, i ${NSIS_MAX_STRLEN}, p $MWOWN_PathBuffer, p 0) i.r0'
  ${If} $0 <= 0
  ${OrIf} $0 >= ${NSIS_MAX_STRLEN}
    StrCpy $MWOWN_LastReason "root_normalization_failed"
    Goto mwown_exit
  ${EndIf}
  System::Call '*$MWOWN_PathBuffer(&w${NSIS_MAX_STRLEN} .r0)'
  StrCpy $MWOWN_RootFull $0
mwown_trim_root:
  StrLen $0 $MWOWN_RootFull
  ${If} $0 <= 3
    StrCpy $MWOWN_LastReason "volume_root_rejected"
    Goto mwown_exit
  ${EndIf}
  StrCpy $1 $MWOWN_RootFull 1 -1
  ${If} $1 == "\"
    StrCpy $MWOWN_RootFull $MWOWN_RootFull -1
    Goto mwown_trim_root
  ${EndIf}

  ; Keep the root open without share-delete until the operation is complete.
  StrCpy $2 $MWOWN_RootFull
  System::Call 'kernel32::CreateFileW(w r2, i 0x80, i 3, p 0, i 3, i 0x02200000, p 0) p.r0'
  ${If} $0 = 0
  ${OrIf} $0 = -1
    StrCpy $MWOWN_LastReason "root_unavailable"
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_RootHandle $0
  System::Call 'kernel32::GetFileInformationByHandle(p $MWOWN_RootHandle, p $MWOWN_Info) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "root_information_failed"
  System::Call '*$MWOWN_Info(i .r1)'
  IntOp $1 $1 & 0x410
  ${If} $1 != 16
    StrCpy $MWOWN_LastReason "root_not_ordinary_directory"
    Goto mwown_exit
  ${EndIf}
  System::Call 'kernel32::GetFinalPathNameByHandleW(p $MWOWN_RootHandle, p $MWOWN_PathBuffer, i ${NSIS_MAX_STRLEN}, i 0) i.r0'
  ${If} $0 <= 0
  ${OrIf} $0 >= ${NSIS_MAX_STRLEN}
    StrCpy $MWOWN_LastReason "root_identity_unavailable"
    Goto mwown_exit
  ${EndIf}
  System::Call '*$MWOWN_PathBuffer(&w${NSIS_MAX_STRLEN} .r0)'
  StrCpy $MWOWN_RootPhysical $0
  StrCpy $1 "\\?\$MWOWN_RootFull"
  StrCpy $2 $MWOWN_RootPhysical
  System::Call 'kernel32::CompareStringOrdinal(w r2, i -1, w r1, i -1, i 1) i.r0'
  ${If} $0 != 2
    StrCpy $MWOWN_LastReason "root_redirected_or_aliased"
    Goto mwown_exit
  ${EndIf}

  ; Check existing intermediate components; final-path verification below also
  ; rejects redirection between the initial root spelling and the opened leaf.
  StrCpy $MWOWN_Index 0
  StrCpy $MWOWN_Component ""
  StrCpy $MWOWN_Prefix $MWOWN_RootFull
mwown_parent_char:
  StrCpy $0 $MWOWN_Relative 1 $MWOWN_Index
  ${If} $0 == "\"
    StrCpy $MWOWN_Prefix "$MWOWN_Prefix\$MWOWN_Component"
    StrCpy $2 $MWOWN_Prefix
    System::Call 'kernel32::GetFileAttributesW(w r2) i.r0 ?e'
    Pop $1
    ${If} $0 = -1
      ${If} $1 = 2
      ${OrIf} $1 = 3
        StrCpy $MWOWN_Result ${MWOWN_MISSING}
        StrCpy $MWOWN_LastReason "parent_missing"
      ${Else}
        StrCpy $MWOWN_LastReason "parent_unavailable"
      ${EndIf}
      Goto mwown_exit
    ${EndIf}
    IntOp $0 $0 & 0x410
    ${If} $0 != 16
      StrCpy $MWOWN_LastReason "parent_not_ordinary_directory"
      Goto mwown_exit
    ${EndIf}
    StrCpy $MWOWN_Component ""
  ${Else}
    StrCpy $MWOWN_Component "$MWOWN_Component$0"
  ${EndIf}
  IntOp $MWOWN_Index $MWOWN_Index + 1
  ${If} $MWOWN_Index < $MWOWN_Length
    Goto mwown_parent_char
  ${EndIf}

  StrCpy $MWOWN_Path "$MWOWN_RootFull\$MWOWN_Relative"
  StrCpy $MWOWN_ExpectedPhysical "$MWOWN_RootPhysical\$MWOWN_Relative"
  StrCpy $3 0x80000000
  ${If} $MWOWN_Mode == "1"
    StrCpy $3 0x80010000
  ${EndIf}
  ; CHECK and OBSERVE request no DELETE access. Every mode denies
  ; share-write/share-delete.
  StrCpy $2 $MWOWN_Path
  System::Call 'kernel32::CreateFileW(w r2, i r3, i 1, p 0, i 3, i 0x08200000, p 0) p.r0 ?e'
  Pop $1
  ${If} $0 = 0
  ${OrIf} $0 = -1
    ${If} $1 = 2
    ${OrIf} $1 = 3
      StrCpy $MWOWN_Result ${MWOWN_MISSING}
      StrCpy $MWOWN_LastReason "file_missing"
    ${Else}
      StrCpy $MWOWN_LastReason "file_unavailable"
    ${EndIf}
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_FileHandle $0
  System::Call 'kernel32::GetFileType(p $MWOWN_FileHandle) i.r0'
  ${If} $0 != 1
    StrCpy $MWOWN_LastReason "file_not_disk"
    Goto mwown_exit
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p $MWOWN_FileHandle, p $MWOWN_Info) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "file_information_failed"
  System::Call '*$MWOWN_Info(i .r1)'
  IntOp $1 $1 & 0x410
  ${If} $1 != 0
    StrCpy $MWOWN_LastReason "file_not_ordinary"
    Goto mwown_exit
  ${EndIf}
  System::Call 'kernel32::GetFinalPathNameByHandleW(p $MWOWN_FileHandle, p $MWOWN_PathBuffer, i ${NSIS_MAX_STRLEN}, i 0) i.r0'
  ${If} $0 <= 0
  ${OrIf} $0 >= ${NSIS_MAX_STRLEN}
    StrCpy $MWOWN_LastReason "file_identity_unavailable"
    Goto mwown_exit
  ${EndIf}
  System::Call '*$MWOWN_PathBuffer(&w${NSIS_MAX_STRLEN} .r1)'
  StrCpy $2 $MWOWN_ExpectedPhysical
  System::Call 'kernel32::CompareStringOrdinal(w r1, i -1, w r2, i -1, i 1) i.r0'
  ${If} $0 != 2
    StrCpy $MWOWN_LastReason "file_redirected_or_aliased"
    Goto mwown_exit
  ${EndIf}
  StrCpy $1 -1
  System::Call 'kernel32::GetFileSizeEx(p $MWOWN_FileHandle, *l.r1) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "file_size_unavailable"
  ${If} $MWOWN_Mode == "2"
    System::Int64Op $1 "<" 1
    Pop $0
    ${If} $0 != "0"
      StrCpy $MWOWN_LastReason "new_self_size_invalid"
      Goto mwown_exit
    ${EndIf}
    System::Int64Op $1 ">" ${MWOWN_MAX_FILE_BYTES}
    Pop $0
    ${If} $0 != "0"
      StrCpy $MWOWN_LastReason "new_self_size_limit"
      Goto mwown_exit
    ${EndIf}
    StrCpy $MWOWN_Size $1
  ${ElseIf} $1 != $MWOWN_Size
    StrCpy $MWOWN_LastReason "size_mismatch"
    Goto mwown_exit
  ${EndIf}

  ; System::Call uses the system-directory DLL, never an install-directory DLL.
  ; CNG owns its hash-object allocation (Windows 7 and newer).
  StrCpy $0 "call_failed"
  StrCpy $1 0
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptOpenAlgorithmProvider(*p.r1, w "SHA256", p 0, i 0) i.r0'
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_provider_unavailable"
  ${If} $1 = 0
    StrCpy $MWOWN_LastReason "sha256_provider_invalid"
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_Algorithm $1
  StrCpy $0 "call_failed"
  StrCpy $1 0
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptCreateHash(p $MWOWN_Algorithm, *p.r1, p 0, i 0, p 0, i 0, i 0) i.r0'
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_create_failed"
  ${If} $1 = 0
    StrCpy $MWOWN_LastReason "sha256_handle_invalid"
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_Hash $1
  System::Alloc 65536
  Pop $MWOWN_Buffer
  ${If} $MWOWN_Buffer = 0
    StrCpy $MWOWN_LastReason "allocation_failed"
    Goto mwown_exit
  ${EndIf}
  System::Alloc 32
  Pop $MWOWN_Digest
  ${If} $MWOWN_Digest = 0
    StrCpy $MWOWN_LastReason "allocation_failed"
    Goto mwown_exit
  ${EndIf}

  StrCpy $MWOWN_Remaining $MWOWN_Size
mwown_hash_next:
  ${If} $MWOWN_Remaining = 0
    Goto mwown_hash_finish
  ${EndIf}
  StrCpy $MWOWN_Want $MWOWN_Remaining
  ${If} $MWOWN_Want > 65536
    StrCpy $MWOWN_Want 65536
  ${EndIf}
  StrCpy $1 0
  System::Call 'kernel32::ReadFile(p $MWOWN_FileHandle, p $MWOWN_Buffer, i $MWOWN_Want, *i.r1, p 0) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "file_read_failed"
  StrCpy $MWOWN_Got $1
  ${If} $MWOWN_Got <= 0
  ${OrIf} $MWOWN_Got > $MWOWN_Want
    StrCpy $MWOWN_LastReason "file_read_count_invalid"
    Goto mwown_exit
  ${EndIf}
  StrCpy $0 "call_failed"
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptHashData(p $MWOWN_Hash, p $MWOWN_Buffer, i $MWOWN_Got, i 0) i.r0'
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_update_failed"
  IntOp $MWOWN_Remaining $MWOWN_Remaining - $MWOWN_Got
  Goto mwown_hash_next

mwown_hash_finish:
  StrCpy $1 -1
  System::Call 'kernel32::GetFileSizeEx(p $MWOWN_FileHandle, *l.r1) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "file_size_recheck_failed"
  ${If} $1 != $MWOWN_Size
    StrCpy $MWOWN_LastReason "size_changed"
    Goto mwown_exit
  ${EndIf}
  StrCpy $0 "call_failed"
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptFinishHash(p $MWOWN_Hash, p $MWOWN_Digest, i 32, i 0) i.r0'
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_finish_failed"
  StrCpy $MWOWN_ActualSha ""
  StrCpy $MWOWN_Index 0
mwown_hex_byte:
  System::Int64Op $MWOWN_Digest "+" $MWOWN_Index
  Pop $MWOWN_HexPointer
  System::Call '*$MWOWN_HexPointer(&i1 .r0)'
  IntOp $0 $0 & 255
  IntFmt $1 "%02x" $0
  StrCpy $MWOWN_ActualSha "$MWOWN_ActualSha$1"
  IntOp $MWOWN_Index $MWOWN_Index + 1
  ${If} $MWOWN_Index < 32
    Goto mwown_hex_byte
  ${EndIf}
  ${If} $MWOWN_Mode != "2"
  ${AndIf} $MWOWN_ActualSha != $MWOWN_ExpectedSha
    StrCpy $MWOWN_LastReason "hash_mismatch"
    Goto mwown_exit
  ${EndIf}

  ; Release CNG resources before any disposition mutation. Failed cleanup
  ; preserves the file and is not retried through the common cleanup path.
  StrCpy $0 "call_failed"
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptDestroyHash(p $MWOWN_Hash) i.r0'
  StrCpy $MWOWN_Hash 0
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_release_failed"
  StrCpy $0 "call_failed"
  System::Call '"$SYSDIR\bcrypt.dll"::BCryptCloseAlgorithmProvider(p $MWOWN_Algorithm, i 0) i.r0'
  StrCpy $MWOWN_Algorithm 0
  !insertmacro MWOWN_REQUIRE_STATUS "sha256_provider_release_failed"

  ; A second spelling check rejects an ancestor moved during the bounded read.
  ; This is not a transaction with a later installer File or ExecWait command.
  System::Call 'kernel32::GetFinalPathNameByHandleW(p $MWOWN_FileHandle, p $MWOWN_PathBuffer, i ${NSIS_MAX_STRLEN}, i 0) i.r0'
  ${If} $0 <= 0
  ${OrIf} $0 >= ${NSIS_MAX_STRLEN}
    StrCpy $MWOWN_LastReason "file_identity_recheck_failed"
    Goto mwown_exit
  ${EndIf}
  System::Call '*$MWOWN_PathBuffer(&w${NSIS_MAX_STRLEN} .r1)'
  StrCpy $2 $MWOWN_ExpectedPhysical
  System::Call 'kernel32::CompareStringOrdinal(w r1, i -1, w r2, i -1, i 1) i.r0'
  ${If} $0 != 2
    StrCpy $MWOWN_LastReason "file_identity_changed"
    Goto mwown_exit
  ${EndIf}

  ${If} $MWOWN_Mode == "2"
    ; The ordinary, bounded staged file was hashed and its identity rechecked.
    ; Common cleanup must also succeed before the caller may use this observation.
    StrCpy $MWOWN_Result ${MWOWN_OBSERVED_NEW_SELF}
    StrCpy $MWOWN_LastReason "new_self_observed"
    Goto mwown_exit
  ${EndIf}

  ${If} $MWOWN_Mode == "0"
    StrCpy $MWOWN_Result ${MWOWN_MATCHED}
    StrCpy $MWOWN_LastReason "matched"
    Goto mwown_exit
  ${EndIf}

  ; FILE_DISPOSITION_INFO contains one BOOLEAN. Use the same file handle that
  ; supplied all bytes to BCrypt; never fall back to a path-based Delete.
  System::Call '*(&i1 1) p.r0'
  ${If} $0 = 0
    StrCpy $MWOWN_LastReason "allocation_failed"
    Goto mwown_exit
  ${EndIf}
  StrCpy $MWOWN_DeleteInfo $0
  System::Call 'kernel32::SetFileInformationByHandle(p $MWOWN_FileHandle, i 4, p $MWOWN_DeleteInfo, i 1) i.r0'
  !insertmacro MWOWN_REQUIRE_BOOL "delete_refused"
  StrCpy $MWOWN_Result ${MWOWN_DELETE_MARKED}
  StrCpy $MWOWN_LastReason "delete_marked"

mwown_exit:
  ; Each release is attempted once. No UNKNOWN disposition is retried.
  ; A failed pre-disposition cleanup must not retain a content-mismatch reason:
  ; callers may compare an admitted predecessor only after a clean CHECK.
  ${If} $MWOWN_Hash != 0
    StrCpy $0 "call_failed"
    System::Call '"$SYSDIR\bcrypt.dll"::BCryptDestroyHash(p $MWOWN_Hash) i.r0'
    StrCpy $MWOWN_Hash 0
    ${If} $0 != "0"
      DetailPrint "File ownership: hash handle cleanup failed"
      StrCpy $MWOWN_Result ${MWOWN_PRESERVED}
      StrCpy $MWOWN_LastReason "sha256_cleanup_failed"
    ${EndIf}
  ${EndIf}
  ${If} $MWOWN_Algorithm != 0
    StrCpy $0 "call_failed"
    System::Call '"$SYSDIR\bcrypt.dll"::BCryptCloseAlgorithmProvider(p $MWOWN_Algorithm, i 0) i.r0'
    StrCpy $MWOWN_Algorithm 0
    ${If} $0 != "0"
      DetailPrint "File ownership: algorithm handle cleanup failed"
      StrCpy $MWOWN_Result ${MWOWN_PRESERVED}
      StrCpy $MWOWN_LastReason "sha256_provider_cleanup_failed"
    ${EndIf}
  ${EndIf}
  ${If} $MWOWN_FileHandle != 0
    System::Call 'kernel32::CloseHandle(p $MWOWN_FileHandle) i.r0'
    StrCpy $MWOWN_FileHandle 0
    ${If} $0 = 0
      ${If} $MWOWN_Result = ${MWOWN_DELETE_MARKED}
        StrCpy $MWOWN_Result ${MWOWN_DELETE_UNCERTAIN}
        StrCpy $MWOWN_LastReason "close_after_delete_failed"
      ${Else}
        StrCpy $MWOWN_Result ${MWOWN_PRESERVED}
        StrCpy $MWOWN_LastReason "file_close_failed"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ${If} $MWOWN_RootHandle != 0
    System::Call 'kernel32::CloseHandle(p $MWOWN_RootHandle) i.r0'
    StrCpy $MWOWN_RootHandle 0
    ${If} $0 = 0
      ${If} $MWOWN_Result = ${MWOWN_DELETE_MARKED}
        StrCpy $MWOWN_Result ${MWOWN_DELETE_UNCERTAIN}
        StrCpy $MWOWN_LastReason "root_close_after_delete_failed"
      ${Else}
        ${If} $MWOWN_Result != ${MWOWN_DELETE_UNCERTAIN}
          StrCpy $MWOWN_Result ${MWOWN_PRESERVED}
          StrCpy $MWOWN_LastReason "root_close_failed"
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ${If} $MWOWN_DeleteInfo != 0
    System::Free $MWOWN_DeleteInfo
    StrCpy $MWOWN_DeleteInfo 0
  ${EndIf}
  ${If} $MWOWN_Digest != 0
    System::Free $MWOWN_Digest
    StrCpy $MWOWN_Digest 0
  ${EndIf}
  ${If} $MWOWN_Buffer != 0
    System::Free $MWOWN_Buffer
    StrCpy $MWOWN_Buffer 0
  ${EndIf}
  ${If} $MWOWN_Info != 0
    System::Free $MWOWN_Info
    StrCpy $MWOWN_Info 0
  ${EndIf}
  ${If} $MWOWN_PathBuffer != 0
    System::Free $MWOWN_PathBuffer
    StrCpy $MWOWN_PathBuffer 0
  ${EndIf}
  DetailPrint "File ownership: $MWOWN_Relative; result=$MWOWN_Result; reason=$MWOWN_LastReason"
  ${If} $MWOWN_SavedError = 1
    SetErrors
  ${Else}
    ClearErrors
  ${EndIf}
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
  Push $MWOWN_Result
FunctionEnd
!macroend

!insertmacro MWOWN_DEFINE_WORKER ""
!insertmacro MWOWN_DEFINE_WORKER "un."
!endif
