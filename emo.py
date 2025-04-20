from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch


# 0: "平淡語氣"
# 1: "關切語調"
# 2: "開心語調"
# 3: "憤怒語調"
# 4: "悲傷語調"
# 5: "疑問語調"
# 6: "驚奇語調"
# 7: "厭惡語調"


class Emotion:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(
            "Johnson8187/Chinese-Emotion-Small"
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            "Johnson8187/Chinese-Emotion-Small"
        ).to(self.device)

    def predict(self, text):
        inputs = self.tokenizer(
            text, return_tensors="pt", padding=True, truncation=True
        ).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        predicted_class = torch.argmax(outputs.logits).item()
        return predicted_class


if __name__ == "__main__":
    test_texts = ["特別想睡覺"]

    emotion_model = Emotion()
    for text in test_texts:
        emotion = emotion_model.predict(text)
        print(f"文本: {text}\n預測情緒: {emotion}\n")
