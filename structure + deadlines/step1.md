Milestone 1: Natural Language Parser (Probably most difficult)
Prove LLM can translate into plain english

```python
from pydantic import BaseModel, Field
from typing import Optional

class ReservationIntent(BaseModel):
    restaurant: str = Field(description="Name of the restaurant or café")
    party_size: int = Field(description="Number of guests")
    date: str = Field(description="Target date in YYYY-MM-DD format")
    preferred_time: str = Field(description="Target time, e.g., 19:00")
    missing_info: Optional[str] = Field(description="Clarifying question if key details are missing")
```
1. Set up Mini Python / Fast API (or node.js with vercel)
2. define target schema (from above)
3. Connect OpenAI/Claude via `instructor` or native Structure outputs.
    - have test inputs and verify the output yields clean.

HAVE THIS DONE 8/20/26