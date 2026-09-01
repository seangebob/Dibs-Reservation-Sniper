# Milestone 2: Core Platform Adapters
isolate rservation lookup logic behind a Python or typescript interface, instead of attempting to web scrape completely

1. create base adapter inference
```python
class ReservationAdapter:
    def search_availability(self, restaurant: str, date: str, party_size: int):
        raise NotImplementedError

    def book_slot(self, slot_id: str, user_credentials: dict):
        raise NotImplementedError
```

2. Implement mock search functions first (returning time dummy slots), replace them with actual API calls or scraping for OpenTable, Resy, or direct booking endpoints

**HAVE THIS DONE 8/27/26***