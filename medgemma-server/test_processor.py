from transformers import AutoProcessor

MODEL_ID = "google/medgemma-4b-it"

print("Downloading/loading processor...")
processor = AutoProcessor.from_pretrained(MODEL_ID)

print("Processor loaded successfully")
print(type(processor).__name__)