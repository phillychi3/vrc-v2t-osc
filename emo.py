import asyncio
from transformers import AutoTokenizer, AutoModelForSequenceClassification  # type: ignore
import torch
from concurrent.futures import ThreadPoolExecutor


# 0: "平淡語氣"
# 1: "關切語調"
# 2: "開心語調"
# 3: "憤怒語調"
# 4: "悲傷語調"
# 5: "疑問語調"
# 6: "驚奇語調"
# 7: "厭惡語調"


class Emotion:
    def __init__(self, use_async=True):
        self.use_async = use_async
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"Emotion analysis using device: {self.device}")
        self.model_loaded = False
        self.loading_error = None
        self.executor = ThreadPoolExecutor(max_workers=1)

        self.executor.submit(self._load_model)

    def _load_model(self):
        """Load model in background thread"""
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                "Johnson8187/Chinese-Emotion-Small"
            )
            self.model = AutoModelForSequenceClassification.from_pretrained(
                "Johnson8187/Chinese-Emotion-Small"
            ).to(self.device)
            self.model_loaded = True
            print("情緒分析模型載入完成")
        except Exception as e:
            self.loading_error = str(e)
            print(f"載入情緒分析模型失敗: {e}")

    def is_ready(self):
        """Check if the model is loaded and ready to use"""
        return self.model_loaded

    def get_loading_error(self):
        """Return any error that occurred during loading"""
        return self.loading_error

    def predict(self, text) -> int:
        """Synchronously predict emotion from text"""
        if not self.model_loaded:
            raise RuntimeError("情緒分析模型尚未載入完成")

        inputs = self.tokenizer(
            text, return_tensors="pt", padding=True, truncation=True
        ).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        predicted_class = int(torch.argmax(outputs.logits).item())
        return predicted_class

    async def predict_async(self, text):
        """Asynchronously predict emotion from text"""
        if not self.model_loaded:
            raise RuntimeError("情緒分析模型尚未載入完成")

        # Run prediction in a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(self.executor, self.predict, text)
        return result


if __name__ == "__main__":
    test_texts = ["特別想睡覺"]

    emotion_model = Emotion()
    for text in test_texts:
        emotion = emotion_model.predict(text)
        print(f"文本: {text}\n預測情緒: {emotion}\n")
