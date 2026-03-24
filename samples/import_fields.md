# Corvus Markdown Import Fields Reference

This file demonstrates all the fields that can be extracted during markdown import in Corvus. Each section shows different import features and how they map to card fields.

## Basic Card Structure

### What is the capital of France? #card
Paris

### What is 2 + 2? #card
4

## Hierarchical Context (context_md field)

# Mathematics
## Algebra
### What is the quadratic formula? #card
x = (-b ± √(b² - 4ac)) / 2a

### Solve: x² + 5x + 6 = 0 #card
(x + 2)(x + 3) = 0
x = -2 or x = -3

## External IDs (external_key field)

### What is the speed of light? #card
id:: physics_speed_of_light
299,792,458 meters per second

### What is Planck's constant? #card
id:: physics_planck_constant
6.626 × 10⁻³⁴ J⋅s

## Tags (tags field)

### What is photosynthesis? #card
tags:: biology, plants, science
The process by which plants convert light energy into chemical energy.

### What is mitosis? #card
tags:: biology, cell division
The process of cell division that results in two identical daughter cells.

## Import IDs (import_id field)

### What is the largest planet? #card id:1
Jupiter

### What is the smallest planet? #card id:2
Mercury

### What is Earth's moon called? #card id:3
Luna (or simply "the Moon")

## Combined Fields Example

# Geography
## European Countries
### What is the capital of Germany? #card id:4
id:: geo_germany_capital
tags:: geography, europe, capitals
Berlin

### What is the capital of Italy? #card id:5
id:: geo_italy_capital
tags:: geography, europe, capitals
Rome

## Media References (media field)

### What does a cat look like? #card
![A domestic cat](cat.jpg)
This is a common house cat.

### What does a dog look like? #card
![[dog.png]]
This is a common dog breed.

## Reversed Cards (allow_reverse)

### Spanish: Hello #card/reverse
Hola

### Spanish: Goodbye #card/reverse
Adiós

### French: Thank you #card/reverse
Merci

## Custom Card Types

### Define: Algorithm #definition-card
A set of rules or processes followed by a computer.

### Define: Database #definition-card
An organized collection of data stored electronically.

### What is recursion? #cloze-card
Recursion is a programming technique where a {{c1::function}} calls {{c2::itself}} to solve a problem.

## Multiple Cards with Context

# Programming
## Python
### What is Python? #card
id:: prog_python_intro
tags:: programming, python, languages
Python is a high-level programming language known for its simplicity and readability.

### What is a list in Python? #card
id:: prog_python_list
tags:: programming, python, data-structures
A list is a mutable sequence of elements enclosed in square brackets: [1, 2, 3]

### What is a dictionary in Python? #card
id:: prog_python_dict
tags:: programming, python, data-structures
A dictionary is a collection of key-value pairs: {"key": "value"}

## JavaScript
### What is JavaScript? #card
id:: prog_js_intro
tags:: programming, javascript, languages
JavaScript is a programming language commonly used for web development.

### What is a variable in JS? #card
id:: prog_js_variable
tags:: programming, javascript, basics
A variable stores data: let x = 5;

## Error Examples (will show in errors field)

### This card has no content #card
# This heading breaks the card structure

### Another broken card #card
# Another heading that shouldn't be here

## Advanced Features

### Card with multiple tags #card
tags:: tag1, tag2, tag3; tag4
This card has multiple tags separated by commas and semicolons.

### Card with complex ID #card
id:: complex-id-with-dashes-and-numbers-123
This demonstrates that IDs can contain dashes, underscores, and numbers.

### Long form content #card id:10
id:: long_form_example
tags:: examples, long-content

This is a longer piece of content that demonstrates how multi-line card content is handled.

It can span multiple paragraphs and include various formatting.

- Bullet points
- **Bold text**
- *Italic text*

All of this content will be included in the back_md field.

## Long Card Example (#long-card)

# Science
## Physics
### Energy concept
#long-card id:123
the law of conservation of energy states that energy cannot be created or destroyed, only transformed.

This is a second paragraph of explanation.

```
E = mc^2

# not a marker, just a blank line inside code block is allowed


```

Final wrap-up paragraph.



*Note:* two consecutive blank lines terminate the `#long-card` content, and they are not part of the imported back text.
