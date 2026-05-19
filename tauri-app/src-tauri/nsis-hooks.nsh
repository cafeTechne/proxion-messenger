; R17.4: Register proxion:// URL scheme on install, unregister on uninstall

!macro NSIS_HOOK_INSTFILES
    WriteRegStr HKCU "Software\Classes\proxion" "" "URL:Proxion Protocol"
    WriteRegStr HKCU "Software\Classes\proxion" "URL Protocol" ""
    WriteRegStr HKCU "Software\Classes\proxion\shell" "" ""
    WriteRegStr HKCU "Software\Classes\proxion\shell\open" "" ""
    WriteRegStr HKCU "Software\Classes\proxion\shell\open\command" "" '"$INSTDIR\proxion-app.exe" "%1"'
!macroend

!macro NSIS_HOOK_UNINSTFILES
    DeleteRegKey HKCU "Software\Classes\proxion"
!macroend
