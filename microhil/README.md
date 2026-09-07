# microHIL – Test-/HIL-Geraet (STM32F446)

Treiber-Bibliothek fuer den microHIL: 4 Relais, 8 Digitalausgaenge,
8 Digitaleingaenge, 4 Analogeingaenge, 2 Analogausgaenge (DAC), 2
schaltbare 12V-Ausgaenge mit Stromsense und 4 PWM-Kanaele. Kommunikation
ueber USB-CDC (virtueller COM-Port), ASCII-Zeilenprotokoll.
`MicroHIL.open_first()` findet den Port automatisch, unter Windows als
`COMx`, unter Linux als `/dev/ttyACMx`.

**Nicht gegen echte Hardware verifiziert** (kein Geraet verfuegbar) –
ausschliesslich aus `docs/protocol.md`/`docs/can-usb.md` des microHIL-Repos
implementiert. Anders als bei hcs34xx/korad_kel102 also ohne
Hardware-Bestaetigung.

**Nicht in die GUI (`lab_gui/`) eingebunden.** Dieser Treiber ist bewusst
eigenstaendig, analog zu den anderen Geraetetreibern nutzbar, aber (noch)
nicht an `device_worker.py`/Dashboard/Testcase-Editor angeschlossen.

## Verwendung

```python
from driver import MicroHIL

with MicroHIL.open_first() as hil:
    print(hil.identify())          # "microHIL,fw=0.1.0"
    hil.set_relay(1, True)
    print(hil.get_relay(1))        # True
    print(hil.get_inputs())        # [IN1, IN2, ..., IN8]
    hil.set_pwm(1, 500)            # 50% Duty-Cycle
```

## Bekannte Eigenheiten / Einschraenkungen

- **Zwei COM-Ports, gleiche VID:PID (0483:5740).** microHIL meldet sich als
  USB-Composite-Device mit zwei CDC-ACM-Funktionen: Interface 0 spricht
  dieses Protokoll, Interface 2 ist der CAN1/SLCAN-Port (siehe
  `can_bus/driver.py` fuer CAN allgemein). `discover()` unterscheidet beide
  anhand der aus `ListPortInfo` extrahierten Interface-Nummer (`MI_00`/
  `MI_02` unter Windows, `-if00`/`-if02` bzw. `LOCATION=` unter Linux). Auf
  echter Hardware beobachtet: der erste Port traegt teils *gar keine*
  MI_xx-/LOCATION-Kennung, und die Interface-Nummer im LOCATION-String steht
  nicht immer in einem festen Format (z. B. `...:x.2` statt `...:0.2`) –
  `_interface_number()` verankert deshalb am Stringende statt ein festes
  Format vorauszusetzen, und `discover()` verteilt Ports ohne bestimmbare
  Nummer als letzten Ausweg sortiert auf die noch offenen Interface-Slots.
  Ist nur ein Port sichtbar (aeltere Single-Port-Firmware oder der CAN-Port
  noch nicht enumeriert), wird dieser ohne weitere Pruefung genommen. Bleibt
  danach noch echte Mehrdeutigkeit, wirft `discover()`/`open_first()` einen
  `HilError` statt zu raten – dann Port explizit an `MicroHIL(port=...)`
  uebergeben.
- **`AOUT`/`PWM`: Wert wird geklemmt, nicht abgelehnt.** Ein
  Kanal-/Indexfehler liefert `ERR RANGE`, ein zu grosser/kleiner mV- bzw.
  Promille-Wert dagegen wird von der Firmware still auf den gueltigen
  Bereich geklemmt (`OK` bestaetigt nur den Kanalindex). Der Treiber
  klemmt deshalb bereits client-seitig auf denselben Bereich
  (`set_analog_output()`/`set_pwm()`), damit der hier sichtbare Sollwert
  mit dem tatsaechlich angewendeten uebereinstimmt. Fuer `PWM` existiert
  zusaetzlich `PWM?`/`get_pwm()` zum Zuruecklesen des tatsaechlichen
  Werts vom Geraet, fuer `AOUT` nicht (kein `AOUT?`).
- **Verriegelung PWM1-4 / OUT1-4.** Beide treiben laut Schaltplan dieselbe
  Endstufe: `set_output(n, True)` (n=1-4) schaltet PWM-Kanal n zwangsweise
  aus, `set_pwm(n, >0)` schaltet OUT n zwangsweise aus – ohne eigene
  Fehlermeldung.
- **`get_current_sense_mv()` liefert keine mA.** Der Shunt-/
  Verstaerkungsfaktor der Stromsense-Schaltung ist laut protocol.md noch
  nicht in der Firmware hinterlegt, `CURR?` liefert die rohe
  Sense-Spannung in mV. Der Methodenname spiegelt das bewusst wider.
- **`AIN?`/`CURR?` bis zu ~10 ms.** Beide loesen eine
  Single-Conversion-ADC-Messung aus, alle anderen Kommandos antworten
  praktisch sofort (GPIO-/Register-Operationen).
- Kein eigener Exception-Typ pro Fehlergrund (`ARGS`/`RANGE`/`UNKNOWN`) –
  anders als `PowerSupplyValueError` bei hcs34xx zeigen alle drei einen
  Programmierfehler im Aufruf an (z.B. falscher Kanalindex), keinen zur
  Laufzeit veraenderlichen Geraetezustand. Der Grund steht trotzdem als
  `HilError.reason` zur Verfuegung. Kanal-/Indexfehler werden vom Treiber
  ausserdem bereits client-seitig per `ValueError` abgefangen, bevor
  ueberhaupt ein `ERR RANGE` vom Geraet moeglich waere.

## Naechste Schritte

- GUI-Integration (`lab_gui/device_worker.py` + Dashboard/Control-Tab).
- Verifikation gegen echte Hardware, sobald verfuegbar.
- `mock.py` (Simulator wie bei hcs34xx/korad_kel102) fuer GUI-Tests ohne
  Hardware, sobald die GUI-Integration ansteht.
