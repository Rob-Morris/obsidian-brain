# Colours

Brain vaults use CSS snippets to colour-code the Obsidian file explorer. Each folder type gets a distinct visual treatment so the vault hierarchy is scannable at a glance.

## Palette

All colours are CSS custom properties in `:root`. The palette provides 16 pastel colours:

| Variable | Hex | Description |
|---|---|---|
| `--palette-sky` | `#8BB8E8` | Pastel Blue |
| `--palette-amber` | `#F5C97A` | Pastel Amber |
| `--palette-sage` | `#8FCA8E` | Pastel Green |
| `--palette-coral` | `#F0908F` | Pastel Coral |
| `--palette-teal` | `#7DD6D2` | Pastel Teal |
| `--palette-rose` | `#F2A8C4` | Pastel Rose |
| `--palette-rose-gold` | `#EDBEA7` | Rose Gold |
| `--palette-peach` | `#F5B88A` | Pastel Peach |
| `--palette-lavender` | `#B8A9E8` | Pastel Lavender |
| `--palette-mint` | `#A8E8D0` | Pastel Mint |
| `--palette-gold` | `#E8D48A` | Pastel Gold |
| `--palette-slate` | `#A0B0C0` | Pastel Slate |
| `--palette-mauve` | `#D4A0C0` | Pastel Mauve |
| `--palette-lime` | `#C4E88A` | Pastel Lime |
| `--palette-steel` | `#8AA8C8` | Pastel Steel |
| `--palette-blush` | `#E8B8B0` | Pastel Blush |
| `--palette-violet-light` | `#C4A8E8` | Light Purple |
| `--palette-violet-dark` | `#3B2060` | Deep Purple |

## Four-Tier Styling

| Type | Background | Foreground | Border |
|---|---|---|---|
| **Attachments** (`_Attachments/`) | Slate 12% tint | Slate text | 3px slate left |
| **Config** (`_Config/`) | Purple 12% tint | Light purple text | 3px purple left |
| **Temporal** (`_Temporal/`) | Steel 12% tint | Per-child colour | 3px coloured left |
| **Plugins** (`_Plugins/`) | Gold 12% tint | Gold text | 3px gold left |
| **Artefact** (root-level) | Rose gold 12% tint | Unique colour per folder | 3px coloured left |

All four tiers get a tinted background and coloured border.

## Theme Variables

System-level theme variables:

```css
--theme-attachments-fg: var(--palette-slate);
--theme-attachments-bg: rgba(160, 176, 192, 0.12);
--theme-config-fg: var(--palette-violet-light);
--theme-config-bg: rgba(196, 168, 232, 0.12);
--theme-temporal-fg: var(--palette-steel);
--theme-temporal-bg: rgba(138, 168, 200, 0.12);
--theme-plugins-fg: var(--palette-gold);
--theme-plugins-bg: rgba(232, 212, 138, 0.12);
--theme-artefact-bg: rgba(237, 190, 167, 0.12);
```

Per-folder colour variables follow `--color-{folder}` for artefacts and `--color-temporal-{folder}` for temporal children.

## Temporal Blend Formula

Temporal child folders each get a distinct base hue blended 35% towards steel. This gives visually distinct colours that share a cool tint.

**Formula:** `result = base + (steel - base) × 0.35` per RGB channel.

Example: Amber (`#F5C97A`) blended towards steel (`#8AA8C8`) = `#D0BD95`.

## CSS Selector Templates

### Artefact folder

```css
/* {Folder Name} — folder + subfolders */
.nav-folder-title[data-path="{Folder Name}"] .nav-folder-title-content,
.nav-folder-title[data-path^="{Folder Name}/"] .nav-folder-title-content {
  color: var(--color-{name});
}
.nav-folder-title[data-path="{Folder Name}"],
.nav-folder-title[data-path^="{Folder Name}/"] {
  background-color: var(--theme-artefact-bg);
  border-left: 3px solid var(--color-{name});
  border-radius: 4px;
}
/* {Folder Name} — files */
.nav-file-title[data-path^="{Folder Name}/"] {
  background-color: var(--theme-artefact-bg);
  border-radius: 4px;
}
.nav-file-title[data-path^="{Folder Name}/"] .nav-file-title-content {
  color: var(--color-{name});
}
```

### Temporal child folder

```css
/* _Temporal/{Child} — folder + subfolders */
.nav-folder-title[data-path="_Temporal/{Child}"] .nav-folder-title-content,
.nav-folder-title[data-path^="_Temporal/{Child}/"] .nav-folder-title-content {
  color: var(--color-temporal-{child});
}
.nav-folder-title[data-path="_Temporal/{Child}"],
.nav-folder-title[data-path^="_Temporal/{Child}/"] {
  background-color: var(--theme-temporal-bg);
  border-left: 3px solid var(--color-temporal-{child});
  border-radius: 4px;
}
/* _Temporal/{Child} — files */
.nav-file-title[data-path^="_Temporal/{Child}/"] {
  background-color: var(--theme-temporal-bg);
  border-radius: 4px;
}
.nav-file-title[data-path^="_Temporal/{Child}/"] .nav-file-title-content {
  color: var(--color-temporal-{child});
}
```

## Archive Subfolder Styling

`_Archive/` subfolders within artefact folders use the same slate styling as `_Attachments/` — this visually signals "infrastructure, not active content". Unlike artefact folder CSS which is per-folder, the archive styling uses wildcard selectors so it works for any artefact type's `_Archive/` subfolder without per-folder CSS.

**Ordering:** The `_Archive` block must come AFTER the Artefact Folders section in the CSS file. The artefact selectors (e.g. `[data-path^="Designs/"]`) also match `Designs/_Archive/` — same specificity, so last rule wins.

```css
/* _Archive subfolders — slate text, inherits parent background */
.nav-folder-title[data-path$="/_Archive"] .nav-folder-title-content,
.nav-folder-title[data-path*="/_Archive/"] .nav-folder-title-content {
  color: var(--theme-attachments-fg);
}
.nav-folder-title[data-path$="/_Archive"],
.nav-folder-title[data-path*="/_Archive/"] {
  border-left: 4px double var(--theme-attachments-fg);
}
.nav-file-title[data-path*="/_Archive/"] .nav-file-title-content {
  color: var(--theme-attachments-fg);
  opacity: 0.85;
}
```

## CSS File Location

The active CSS lives at `.obsidian/snippets/folder-colours.css`. Enable via **Settings > Appearance > CSS Snippets** in Obsidian. Instance-specific colour assignments are documented in `_Config/Styles/obsidian.md`.
