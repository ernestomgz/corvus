# Corvus Markdown Import

Corvus imports Markdown notes into fixed front/back cards. Import behavior is intentionally not configurable.

## Supported Markers

Only these markers are valid:

- `#card`: creates one card. The back stops at the first blank line.
- `#card-reverse`: creates two cards. The second card swaps front and back.
- `#long-card`: creates one card. The back may contain single blank lines.
- `#long-card-reverse`: creates two long cards. The second card swaps front and back.

Markers such as `#card/reverse`, `#photo-card`, or custom markers are ignored.

## Basic Cards

```md
What is the capital of France?
#card
Paris
```

The front is the text before the marker. The back is the text after the marker until the first blank line.

The marker can also be inline:

```md
## Capital of France #card
Paris
```

## Reverse Cards

```md
## Capital of France #card-reverse
Paris
```

This creates:

- Front: `Capital of France`, Back: `Paris`
- Front: `Paris`, Back: `Capital of France`

## Long Cards

Use long cards when the back needs paragraphs or single blank lines. Two consecutive blank lines end the card.

```md
# Physics
## Energy
Conservation of energy
#long-card
Energy cannot be created or destroyed.

It can only be transformed.


This text is outside the card.
```

## IDs And Tags

### YAML Frontmatter

Corvus reads a small set of kebab-case YAML frontmatter keys. Unknown keys are allowed and ignored.

Checked keys:

- `card-tags`: imported by Corvus. Tags listed here are applied to every card parsed from that Markdown file.

`card-tags` can be a YAML list:

```md
---
card-tags:
  - calculus
  - exam
---

Derivative of x^2
#card
2x
```

or a comma/semicolon-separated string:

```md
---
card-tags: calculus, exam
---

Derivative of sin(x)
#card
cos(x)
```

Card-level `tags::` lines are still supported and are combined with `card-tags`:

```md
---
card-tags:
  - calculus
---

Derivative of e^x
#card
tags:: exam
e^x
```

On update, imported tags are replaced by the current imported tags. Removing a tag from frontmatter removes it from the updated card.

Inline IDs are supported:

```md
Capital of Spain #card id:1a2b
Madrid
```

Metadata lines after the marker are also supported:

```md
Capital of Germany
#card
id:: geo_germany_capital
tags:: geography, europe
Berlin
```

For reverse cards, inline IDs can provide one ID for each generated card:

```md
Capital of Italy #card-reverse id:10|11
Rome
```

## Folder To Deck Mapping

Zip files may contain nested folders. Folder paths become deck paths. If no destination deck is selected, each Markdown file must be inside at least one folder.

## Media

Media references are copied into Corvus media storage. Markdown image syntax and Obsidian wiki media syntax are supported:

```md
Identify the diagram
#card
![Diagram](attachments/diagram.png)
```

```md
Identify the image
#card
![[image.png]]
```
