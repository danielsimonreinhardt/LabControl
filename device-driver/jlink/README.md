# J-Link (SEGGER) über den J-Link Commander

Verbund Pi + PC, Stufe V3 (Entscheidung PI-31 a). Treiber für Flashen, Reset, Anhalten und
Starten über den J-Link Commander (`JLink.exe` unter Windows, `JLinkExe` unter Linux), je
Bedienung ein eigener Prozess.

- `driver.py`: `JLink` (Zielbaustein, SWD/JTAG, Takt, optional Seriennummer), `find_commander()`,
  Auswertung der Commander-Ausgabe (`parse_emulators`, `parse_info`, `first_error`).
- `mock.py`: `MockJLink` für den Simulationsmodus und die Prüfungen.

Bewusst ohne Hintergrund-Abfrage: jede Abfrage öffnet die Sonde, und die EDU-Lizenz zeigt einmal je
24 h einen Bestätigungsdialog, der den Commander anhält. Jeder Aufruf hat deshalb ein Zeitlimit
(30 s, Flashen 180 s); die Meldung nennt den Dialog als wahrscheinlichen Grund.

`.bin` wird abgelehnt (bräuchte eine Startadresse); HEX, SREC und ELF tragen sie selbst.

Prüfung ohne J-Link: `tools/check_jlink.py` (nachgebauter Commander).
