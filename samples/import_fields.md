# Corvus Markdown Import Examples

This file demonstrates the fixed Markdown markers supported by Corvus.

## Basic Card

### What is the capital of France? #card
Paris

What is 2 + 2?
#card
4

## Hierarchical Context

# Mathematics
## Algebra
### What is the quadratic formula? #card
x = (-b +/- sqrt(b^2 - 4ac)) / 2a

## IDs And Tags

### What is the speed of light? #card
id:: physics_speed_of_light
tags:: physics, constants
299,792,458 meters per second

### What is the largest planet? #card id:1
Jupiter

## Reverse Cards

### Spanish: Hello #card-reverse
Hola

### Spanish: Goodbye #card-reverse
Adios

### French: Thank you #card-reverse
Merci

## Media References

### What does this image show? #card
![Example image](attachments/example.png)

## Long Cards

# Science
## Physics
### Energy concept
#long-card id:123
The law of conservation of energy states that energy cannot be created or destroyed, only transformed.

This is a second paragraph of explanation.

```
E = mc^2

Blank lines inside fenced code blocks are allowed.
```

Final wrap-up paragraph.


This text is outside the long card.

## Long Reverse Cards

Prompt with paragraphs
#long-card-reverse
Answer line 1

Answer line 2


This text is outside the reverse long card.

Two consecutive blank lines terminate `#long-card` and `#long-card-reverse` content.
