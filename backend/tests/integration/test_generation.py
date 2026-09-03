"""Opt-in smoke test for Nova text generation through Bedrock.

Run from the backend directory:
    python -m tests.integration.test_generation

The test makes one short, paid Bedrock generation call. It does not connect to
PostgreSQL and does not modify any data.
"""

from app.clients.generation import GENERATION_MODEL_ID, generate_text


def run_generation_integration_test() -> None:
    print(f"Calling Bedrock model: {GENERATION_MODEL_ID}")

    answer = generate_text(
        "In one sentence, explain what retrieval-augmented generation is.",
        system_prompt=(
            "You are a concise research assistant. Answer accurately and do "
            "not use more than one sentence."
        ),
        max_tokens=100,
        temperature=0.1,
    )

    assert answer.strip()
    print(f"Response: {answer}")
    print("Bedrock generation integration test passed.")


if __name__ == "__main__":
    run_generation_integration_test()
