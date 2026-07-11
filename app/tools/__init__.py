from app.tools.dining import run_dining_tools
from app.tools.official import run_official_tools
from app.tools.places import run_place_tools


def run_tools(question: str) -> dict:
    tool_results = []
    cards = []

    tool_results.extend(run_official_tools(question))

    place_output = run_place_tools(question)
    tool_results.extend(place_output.get("tool_results", []))
    cards.extend(place_output.get("cards", []))

    dining_output = run_dining_tools(question)
    tool_results.extend(dining_output.get("tool_results", []))
    cards.extend(dining_output.get("cards", []))

    return {
        "tool_results": tool_results,
        "cards": cards,
    }
