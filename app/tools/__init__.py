from app.tools.calendar import run_calendar_tools
from app.tools.checklist import run_checklist_tools
from app.tools.dining import run_dining_tools
from app.tools.official import run_official_tools
from app.tools.parent import run_parent_tools
from app.tools.places import run_place_tools


def run_tools(question: str, history=None, profile=None, location=None) -> dict:
    tool_results = []
    cards = []

    tool_results.extend(run_official_tools(question))

    calendar_output = run_calendar_tools(question)
    tool_results.extend(calendar_output.get("tool_results", []))
    cards.extend(calendar_output.get("cards", []))

    checklist_output = run_checklist_tools(question)
    tool_results.extend(checklist_output.get("tool_results", []))
    cards.extend(checklist_output.get("cards", []))

    parent_output = run_parent_tools(question, profile=profile)
    tool_results.extend(parent_output.get("tool_results", []))
    cards.extend(parent_output.get("cards", []))

    place_output = run_place_tools(question, history=history, profile=profile, location=location)
    tool_results.extend(place_output.get("tool_results", []))
    cards.extend(place_output.get("cards", []))

    dining_output = run_dining_tools(question)
    tool_results.extend(dining_output.get("tool_results", []))
    cards.extend(dining_output.get("cards", []))

    return {
        "tool_results": tool_results,
        "cards": cards,
    }
