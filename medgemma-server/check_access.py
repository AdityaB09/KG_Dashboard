from huggingface_hub import model_info

MODEL_ID = "google/medgemma-4b-it"

try:
    info = model_info(MODEL_ID)
    print("Access successful")
    print("Model:", info.modelId)
    print("Private:", info.private)
    print("Gated:", info.gated)
except Exception as exc:
    print("Access failed")
    print(type(exc).__name__, str(exc))