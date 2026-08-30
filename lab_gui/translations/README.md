# Übersetzungssystem (i18n)

Dieses Verzeichnis enthält die Sprachdateien der LabControl-GUI. Die
Implementierung liegt in [`lab_gui/i18n.py`](../i18n.py).

## Grundidee: Der deutsche Text *ist* der Schlüssel

Es gibt keine künstlichen IDs wie `"btn_apply"`. Stattdessen wird direkt der
deutsche Originaltext als Schlüssel verwendet:

```python
from i18n import tr

self._apply_button.setToolTip(tr("Übernehmen"))
```

`tr()` schlägt `"Übernehmen"` im aktuell geladenen Sprach-Dictionary nach:

```python
def tr(self, text: str, **kwargs) -> str:
    translated = self._strings.get(text, text)   # Fallback: der Key selbst
    if kwargs:
        return translated.format(**kwargs)
    return translated
```

Fehlt der Schlüssel in der aktuellen Sprachdatei, kommt automatisch der
deutsche Originaltext zurück -- ein neuer, noch nicht übersetzter String
zeigt also einfach Deutsch an, statt zu crashen oder leer zu bleiben.

## Die JSON-Dateien

`de.json` und `en.json` sind beide flache
`{deutscher_text: übersetzter_text}`-Wörterbücher, z. B.:

```json
{
  "Übernehmen": "Übernehmen",
  "Setzen": "Set"
}
```

- In **`de.json`** ist Schlüssel = Wert. Das ist funktional überflüssig
  (siehe Fallback oben), dient aber als Dokumentation/Vorlage aller
  vorhandenen Schlüssel.
- In **`en.json`** steht die tatsächliche Übersetzung.

Beide Dateien müssen exakt dieselben Schlüssel enthalten. Beim Anlegen einer
neuen Sprache reicht es, `en.json` zu kopieren und alle Werte zu übersetzen.

## Platzhalter in dynamischen Texten

Für Texte mit eingesetzten Werten steht der Platzhaltername direkt im
Schlüssel, im Python-`str.format()`-Syntax:

```python
tr("Kein Gerät des Typs '{label}' verbunden", label=label)
```

Der Schlüssel `"Kein Gerät des Typs '{label}' verbunden"` muss in **beiden**
JSON-Dateien identisch vorkommen (inklusive `{label}`), sonst schlägt
`.format(label=...)` fehl bzw. bekommt in der Zielsprache nie den Platzhalter
ersetzt. Format-Spezifikationen wie `{value:g}` oder `{samples:.1f}` gehören
ebenfalls zum Schlüssel und werden 1:1 übernommen.

## Sprachwechsel zur Laufzeit (ohne Neustart)

`Translator` (in `i18n.py`) ist ein Singleton mit Signal `language_changed`.
Jedes Widget, das übersetzte Texte anzeigt, folgt demselben Muster wie beim
bestehenden `ThemeManager` für Dark Mode:

```python
Translator.instance().language_changed.connect(self._retranslate)
self._retranslate()
```

`_retranslate()` setzt alle sichtbaren Texte des Widgets neu über `tr(...)`.
Wechselt der Nutzer im Settings-Tab die Sprache, lädt
`Translator.set_language()` die neue JSON-Datei und feuert das Signal --
jedes verbundene Widget aktualisiert sich selbst, ohne dass die App neu
gestartet werden muss.

## Wichtige Falle: Dropdowns/Comboboxen

Bei Comboboxen (z. B. Last-Modus, Aktionen, Signalform) darf der sichtbare
Text **niemals** gleichzeitig als interner Wert dienen -- sonst bricht die
Logik, sobald sich der Text übersetzen lässt. Deshalb gilt in dieser
Codebase durchgängig:

- Interner, sprachunabhängiger Code (z. B. `"CURR"`, `"sine"`) wird als
  `itemData` gespeichert.
- Nur der Anzeigetext kommt aus `tr(...)`.
- Code liest immer `combo.currentData()` / `combo.findData(code)`,
  **niemals** `combo.currentText()`.

Beim Sprachwechsel werden die Combobox-Einträge neu befüllt (`blockSignals`,
`clear()`, neu `addItem(tr(label), code)`), die aktuelle Auswahl aber anhand
des Codes wiederhergestellt (`findData(current_code)`), damit sich an der
tatsächlichen Auswahl nichts ändert.

## Neue übersetzbare Stelle hinzufügen

1. String im Code mit `tr("Deutscher Text")` umschließen (Import:
   `from i18n import tr`).
2. Für dynamische Texte Platzhalter im `{name}`-Format verwenden und als
   Keyword-Argument an `tr()` übergeben.
3. Denselben Schlüssel (Wortlaut exakt wie im Code!) in `de.json` (Wert =
   Schlüssel) und `en.json` (Wert = Übersetzung) eintragen.
4. Falls das Widget neu erzeugt wird (nicht Teil eines bereits
   "retranslate-fähigen" Widgets): `Translator.instance().language_changed`
   verbinden und eine `_retranslate()`-Methode ergänzen, die alle Texte
   dieses Widgets neu setzt.

## Neue Sprache hinzufügen

1. `en.json` nach `<code>.json` kopieren (z. B. `fr.json`) und alle Werte
   übersetzen.
2. In `i18n.py` den Eintrag `AVAILABLE_LANGUAGES["<code>"] = "<Eigenname>"`
   ergänzen (Eigenname = wie die Sprache in ihrer eigenen Schreibweise heißt,
   z. B. `"Français"` -- der Name wird im Dropdown nicht übersetzt).
3. Fertig -- die Sprache erscheint automatisch im Sprach-Dropdown im
   Settings-Tab.

## Vollständigkeit prüfen

Es gibt kein automatisiertes Build-Tool dafür, aber ein einfacher Check: Alle
`tr(...)`-Aufrufe mit Literal-Strings im Code lassen sich per AST einsammeln
und gegen die Schlüssel von `en.json` abgleichen (Dropdown-Basistexte wie
`LOAD_MODES`, `DEVICE_ACTIONS`, `DEVICE_KIND_LABELS`, `FIELD_DEFS` in den
jeweiligen Modulen kommen als Variable in `tr()` an, müssen also von Hand
mitgezählt werden). Fehlende Schlüssel fallen nicht auf -- sie zeigen einfach
weiterhin Deutsch an, auch wenn Englisch gewählt ist.
