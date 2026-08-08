import json
import os
from typing import Optional, TypedDict

from openai import OpenAI
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

from agents.research_agent import ask_agent, SYSTEM_PROMPT as RESEARCH_SYSTEM_PROMPT
from agents.market_agent import analyze_market
from agents.price_action_agent import analyze_price_action

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYNTHESIS_PROMPT = """
You are a crypto research synthesizer. You will be given three independent
reports about the same token: a research report (project overview, market
data, on-chain/DeFi health), a market trend analysis, and a technical price
action analysis.

Write one combined report (plain text, a few short paragraphs) that:
- Leads with the project overview.
- States market and technical trend, noting agreement or disagreement between
  them (e.g. fundamentals look strong but price action is bearish).
- If any input is missing/null, say that angle wasn't available instead of
  omitting it silently.
- Ends with: "This is a summary of available information, not investment advice."

Never invent data not present in the inputs.
"""


class ResearchState(TypedDict, total=False):
    name: Optional[str]
    symbol: Optional[str]
    research_report: Optional[str]
    market_analysis: Optional[dict]
    price_action_analysis: Optional[dict]
    final_report: Optional[str]


def research_node(state: ResearchState) -> dict:
    coin = state.get("name") or state.get("symbol")
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": f"Research the crypto project '{coin}': project overview, market data, and on-chain/DeFi health."},
    ]
    return {"research_report": ask_agent(messages)}


def market_node(state: ResearchState) -> dict:
    result = analyze_market(name=state.get("name"), symbol=state.get("symbol"))
    return {"market_analysis": result.model_dump() if result else None}


def price_action_node(state: ResearchState) -> dict:
    result = analyze_price_action(name=state.get("name"), symbol=state.get("symbol"))
    return {"price_action_analysis": result.model_dump() if result else None}


def synthesis_node(state: ResearchState) -> dict:
    payload = {
        "research_report": state.get("research_report"),
        "market_analysis": state.get("market_analysis"),
        "price_action_analysis": state.get("price_action_analysis"),
    }

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=[
            {"role": "system", "content": SYNTHESIS_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    return {"final_report": response.choices[0].message.content}


def build_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("research", research_node)
    graph.add_node("market", market_node)
    graph.add_node("price_action", price_action_node)
    graph.add_node("synthesis", synthesis_node)

    graph.add_edge(START, "research")
    graph.add_edge(START, "market")
    graph.add_edge(START, "price_action")
    graph.add_edge("research", "synthesis")
    graph.add_edge("market", "synthesis")
    graph.add_edge("price_action", "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()


_app = None


def run_orchestrator(name: Optional[str] = None, symbol: Optional[str] = None) -> ResearchState:
    global _app
    if _app is None:
        _app = build_graph()
    return _app.invoke({"name": name, "symbol": symbol})


if __name__ == "__main__":
    result = run_orchestrator(name="bitcoin")
    print(result["final_report"])
