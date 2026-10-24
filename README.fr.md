# Screenshoter

[Русский](README.md) · [English](README.en.md) · [Español](README.es.md) · [Deutsch](README.de.md) · **Français** · [Português](README.pt.md) · [中文](README.zh.md) · [日本語](README.ja.md)

**Screenshoter** est un outil de capture d'écran rapide pour Windows, dans l'esprit de Lightshot : appuyez sur **PrtScn**, sélectionnez une zone, dessinez dessus des flèches, des cadres et des notes, puis enregistrez ou copiez. Interface sombre moderne, fonctionne depuis la zone de notification. L'interface existe en 8 langues.

![Éditeur de captures](images/fr/editor.png)

## Fonctionnalités

- **Sélection de zone** avec poignées sur les bords et les coins ; le cadre mémorise sa position.
- **Proportions** : libre, 1:1, 4:5, 3:4, 4:3, 16:9, 9:16, 3:2, 2:3, un rapport personnalisé ou une taille exacte en pixels.
- **Dessin** : crayon, ligne, courbe à 3 points, flèche, rectangle, cercle et ellipse ; couleur, transparence, épaisseur, arrondi ; annuler et rétablir.
- Le **curseur de la souris** peut être inclus dans la capture.
- **Enregistrement** en PNG, JPG, WEBP ou BMP avec réglage de la qualité ; copie dans le presse-papiers.
- **Capture à l'enregistrement** : l'écran n'est pas figé, la vidéo continue, l'image est prise au moment de l'enregistrement.
- **Collage** : mode en direct, images légendées « Capture N », bordures noires, assombrissement hors du cadre.
- Plusieurs écrans et mise à l'échelle Windows de 125 à 200 %.
- **8 langues d'interface** : Русский, English, Español, Deutsch, Français, Português, 中文, 日本語 (choix dans les Paramètres).

## Interface

Rangée du haut : proportions, déplacer le cadre, annuler et rétablir, curseur dans la capture, collage, image du collage, enregistrer, enregistrer sous, paramètres, fermer. Rangée du bas (affichée par le bouton flèche) : crayon, ligne, courbe, flèche, formes, couleur, épaisseur, arrondi. Aucun outil ne s'active tout seul ; un second clic le désactive. Le réticule n'apparaît que pendant le dessin.

### Couleur et transparence

![Couleur et transparence](images/fr/colors.png)

Couleurs rapides, palette et curseur de transparence du trait.

### Formes

![Formes](images/fr/shapes.png)

### Paramètres

![Paramètres](images/fr/settings.png)

Format et qualité, assombrissement hors du cadre, « Capture à l'enregistrement » (horloge), raccourcis clavier (clavier) et choix de la langue.

### Raccourcis clavier et à propos

![Raccourcis clavier et à propos](images/fr/hotkeys.png)

## Installation

1. Installez Python 3.9 ou plus récent depuis python.org (cochez « Add Python to PATH »).
2. Lancez `install.bat` : il installe Pillow, pystray et pynput.
3. Lancez `run.bat`. Une icône apparaît dans la zone de notification ; capturez avec **PrtScn**.
4. Pour obtenir un seul `.exe` sans Python, lancez `build_exe.bat` ; le résultat se trouve dans `dist`.

> Démarrage automatique : clic droit sur l'icône → « Démarrer avec Windows ». Sous Windows 11, PrtScn ouvre par défaut l'Outil Capture : désactivez-le dans Paramètres → Accessibilité → Clavier.

## Développement

Python 3, Tkinter, Pillow, pystray, pynput. Code source : `screenshoter.py` et `i18n_data.py` (traductions).

Année de création : 2026. Développeur : Free software · [GitHub](https://github.com/Al235r/Screenshoter).
