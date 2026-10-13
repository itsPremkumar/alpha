from pydantic import BaseModel, Field, model_validator


class C(BaseModel):
    x: bool = Field(default=False)

    @model_validator(mode="after")
    def _check(self):
        if not isinstance(self.x, bool):
            raise ValueError(f"expected bool, got {type(self.x).__name__}")
        return self


for v in (True, False, "true", "false", "1", "yes"):
    try:
        c = C(x=v)
        print(repr(v), "->", c.x)
    except Exception as e:
        print(repr(v), "-> RAISED", type(e).__name__, str(e)[:70])
