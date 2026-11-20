"""Dates named by weekday, worked out in code rather than by the model.

Language models are unreliable at calendar arithmetic: one model read the same
"next Saturday" as two different dates on two runs. So when a prompt names its
day only by weekday, the date comes from here.

- "Saturday", "this Saturday", and "coming Saturday" mean the nearest one,
  today included.
- "Next Saturday" is ambiguous -- said on a Friday, people mean tomorrow or the
  week after -- and a book prompt books at once, so both dates come back and
  the validator asks which. Said on a Saturday, it can only mean a week out.

Anything more involved is left to the model: a prompt that also gives a
calendar date ("Saturday, Dec 5", "the 28th"), talks about weeks ("Saturday
next week"), says today, tonight, or tomorrow, says "after next", or names more
than one weekday.
"""

import re
from datetime import date, timedelta


_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tues": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thurs": 3,
    "thur": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_QUALIFIERS = ("this", "coming", "next", "last", "past", "previous", "following", "every")

_WEEKDAY_PHRASE = re.compile(
    rf"\b(?:({'|'.join(_QUALIFIERS)})\s+)?({'|'.join(_WEEKDAYS)})\b\.?",
    re.IGNORECASE,
)

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

_LEAVE_TO_MODEL = re.compile(
    r"\b(?:weeks?|weekend|fortnight|today|tonight|tomorrow|after\s+next)\b"
    r"|\d{4}-\d{1,2}-\d{1,2}"  # 2026-11-28
    r"|\b\d{1,2}\s*/\s*\d{1,2}\b"  # 11/28
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"  # the 28th
    rf"|\b(?:{_MONTHS})\.?\s+\d{{1,2}}\b"  # Nov 28
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\b",  # 28 Nov
    re.IGNORECASE,
)

#: Qualifiers that point at the nearest such day.
_NEAREST = ("", "this", "coming")


def dates_for_weekday(prompt: str, today: date) -> tuple[date, ...]:
    """The date or dates a weekday-only prompt can mean.

    One date when the phrase is clear, two (earliest first) when it is "next
    <weekday>" and the nearest such day is still to come, and none when the
    prompt doesn't name its day by weekday alone -- the model's date stands.
    """

    if _LEAVE_TO_MODEL.search(prompt):
        return ()

    phrases: set[tuple[str, int]] = set()
    for match in _WEEKDAY_PHRASE.finditer(prompt):
        qualifier = (match.group(1) or "").lower()
        word = match.group(2).lower()
        if not qualifier and not word.endswith("day"):
            # A bare "sat", "sun", or "wed" is more often an ordinary word.
            continue
        phrases.add(("nearest" if qualifier in _NEAREST else qualifier, _WEEKDAYS[word]))

    if len(phrases) != 1:
        return ()
    ((qualifier, weekday),) = phrases

    nearest = today + timedelta(days=(weekday - today.weekday()) % 7)
    if qualifier == "nearest":
        return (nearest,)
    if qualifier == "next":
        if nearest == today:
            return (today + timedelta(days=7),)
        return (nearest, nearest + timedelta(days=7))
    return ()
