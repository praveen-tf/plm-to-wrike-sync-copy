# 📋 Style & Conventions

## Python Style Guide

- **Follow PEP8**: Line length 100 chars, double quotes, trailing commas
- **Always use type hints** for function signatures and class attributes
- **Format with `ruff format`**
- **Use `pydantic` v2** for data validation and settings management
- **Naming**: `snake_case` (vars/functions), `PascalCase` (classes/types), `UPPER_SNAKE_CASE` (constants), `_leading_underscore` (private)

## Docstring Standards

Use Google-style docstrings:

```python
def calculate_discount(price: Decimal, discount_percent: float) -> Decimal:
    """
    Calculate the discounted price for a product.

    Args:
        price: Original price of the product
        discount_percent: Discount percentage (0-100)

    Returns:
        Final price after applying discount

    Raises:
        ValueError: If discount_percent is not between 0 and 100
    """
```

## Documentation Standards

- Every module should have a docstring explaining its purpose
- Public functions must have complete docstrings
- Complex logic should have inline comments with `# Reason:` prefix
