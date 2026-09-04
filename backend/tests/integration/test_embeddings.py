"""Live integration test for Amazon Titan embeddings.

Run from the backend directory:
    python -m tests.integration.test_embeddings
"""

from app.clients.embeddings import DIMENSIONS, embed_text


def run_embedding_integration_test() -> None:
    print("Generating an embedding with Amazon Bedrock...")
    embedding = embed_text(
        "Retrieval-augmented generation for scientific literature."
    )
    assert len(embedding) == DIMENSIONS
    assert all(isinstance(value, (int, float)) for value in embedding)
    print(f"Titan returned a {DIMENSIONS}-dimension vector.")
    print("Embedding integration test passed.")


if __name__ == "__main__":
    run_embedding_integration_test()
