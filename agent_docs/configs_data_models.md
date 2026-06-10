# Configuration Management & Data Models
Follow these best practices for managing configuration and defining data models in your application.

## ⚙️ Configuration Management

Use `pydantic-settings` with `SettingsConfigDict` (Pydantic v2 syntax). Settings are automatically read from environment variables and `.env` files.

### If using .env
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",   # Optional: only loads env vars with this prefix (e.g. APP_DATABASE_URL).
                             # Best practice for namespacing, but a breaking change if env vars already exist without the prefix.
                             # Check with the user and call this out in the README.
        case_sensitive=False,
    )

    # set required fields below — missing values raise a ValidationError at startup.
    database_url: str
    api_key: str
    # set optional fields with a default value.
    app_name: str = "MyApp"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

### lru_cache best practices

`@lru_cache()` ensures `Settings` is only instantiated once. In tests, call `get_settings.cache_clear()` before overriding env vars to force a fresh instance.

For FastAPI projects, prefer dependency injection instead:

```python
# Route
def my_route(settings: Settings = Depends(get_settings)): ...

# Test override
app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-key")
```

## 🏗️ Data Models and Validation
Use Pydantic v2 `BaseModel` for all data models (request/response schemas, domain objects).

Use `BaseSettings` (see above) for app configuration only.

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import datetime
from decimal import Decimal


class Product(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # Enable ORM mode for SQLAlchemy etc.

    # the ... means required (no default); gt, ge, lt, le, min_length, max_length are the supported constraints
    name: str = Field(..., min_length=1, max_length=255) # required no default
    price: Decimal = Field(..., gt=0) # required no default
    discount: Decimal = Field(default=Decimal("0"), ge=0) # optional with default
    category: str # required no default (alternative syntax)
    tags: list[str] = [] # optional, default is an empty list
    weight: float | None = None # optional, default is None

    # For database models
    id: int | None = None
    created_at: datetime | None = None

    # used for complex validation that can't be expressed with simple constraints. Can also be used for sanitization (e.g. stripping whitespace).
    @field_validator("price")
    @classmethod
    def check_price_decimal_places(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("price must have at most 2 decimal places")
        return v

    # cross field validation (e.g. discount must be less than price) requires a model_validator
    @model_validator(mode="after")
    def check_discount_less_than_price(self) -> "Product":
        if self.discount >= self.price:
            raise ValueError("discount must be less than price")
        return self
