from agents.research_agent import ask_agent, SYSTEM_PROMPT

def main():
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    print("Crypto Chatbot - 'exit' to quit")
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        messages.append({"role": "user", "content": user_input})
        reply = ask_agent(messages)
        print(f"Bot: {reply}")
        messages.append({"role": "assistant", "content": reply})

if __name__ == "__main__":
    main()