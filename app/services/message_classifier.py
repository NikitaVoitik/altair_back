import json
import logging
from typing import Dict, Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class MessageClassifier:
    def __init__(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI API key not configured")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def classify_message(self, text: str, source: str = "unknown") -> Dict[str, Any]:
        """
        Classify a message using OpenAI GPT

        Args:
            text: The message text to classify
            source: The source of the message (telegram, web, etc.)

        Returns:
            Dictionary with classification results
        """
        if not self.client:
            logger.error("OpenAI client not initialized - API key missing")
            return self._fallback_classification(text)

        prompt = f"""
        Analyze the following message and classify it.
        
        Source: {source}
        Text: {text}
        
        Return the result in JSON format:
        {{
            "title": "A concise, descriptive title for this message (max 50 characters)",
            "category": "meeting|task|information|thought",
            "confidence": 0.0-1.0,
            "entities": {{
                "dates": ["list of dates found in text"],
                "times": ["list of times found in text"],
                "contact": "main person/contact name (only one, the most relevant)",
                "projects": ["list of project names"],
                "keywords": ["important keywords from the message"]
            }},
            "priority": "low|medium|high",
            "action_required": true/false,
            "summary": "brief summary of the message content"
        }}
        
        Classification criteria:
        - meeting: mentions time, people, meeting place, appointments, calls
        - task: something needs to be done, deadlines, assignments, todos
        - information: reports, notifications, reference information, updates
        - thought: ideas, suggestions, reflections, brainstorming
        
        Priority criteria:
        - high: urgent tasks, important meetings, critical information, deadlines
        - medium: regular tasks, scheduled meetings, useful information
        - low: general thoughts, non-urgent information, casual notes
        
        For contact extraction: Extract only the most relevant person's name from the message. 
        If multiple people are mentioned, choose the primary contact (sender, main person being discussed, or meeting organizer).
        Return null if no specific person is mentioned.
        
        Always respond with valid JSON only.
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an assistant for message classification. Always respond with valid JSON only."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=600
            )

            result = json.loads(response.choices[0].message.content)

            # Validate and normalize the result
            return self._validate_classification_result(result)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAI response as JSON: {e}")
            return self._fallback_classification(text)
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._fallback_classification(text)

    def _validate_classification_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize classification result"""
        # Ensure required fields exist with default values
        validated = {
            "title": result.get("title", "")[:50] or self._generate_fallback_title(
                result.get("category", "information")),
            "category": result.get("category", "information"),
            "confidence": max(0.0, min(1.0, result.get("confidence", 0.5))),
            "entities": {
                "dates": result.get("entities", {}).get("dates", []),
                "times": result.get("entities", {}).get("times", []),
                "contact": result.get("entities", {}).get("contact"),  # Single contact now
                "projects": result.get("entities", {}).get("projects", []),
                "keywords": result.get("entities", {}).get("keywords", [])
            },
            "priority": result.get("priority", "medium"),
            "action_required": result.get("action_required", False),
            "summary": result.get("summary", "")[:500]  # Limit summary length
        }

        # Ensure valid category values
        valid_categories = ["meeting", "task", "information", "thought"]
        if validated["category"] not in valid_categories:
            validated["category"] = "information"

        # Ensure valid priority values
        valid_priorities = ["low", "medium", "high"]
        if validated["priority"] not in valid_priorities:
            validated["priority"] = "medium"

        return validated

    def _generate_fallback_title(self, category: str) -> str:
        """Generate a simple fallback title based on category"""
        titles = {
            "meeting": "Meeting Item",
            "task": "Task Item",
            "information": "Information Item",
            "thought": "Thought Item"
        }
        return titles.get(category, "New Item")

    def _fallback_classification(self, text: str) -> Dict[str, Any]:
        """Simple fallback logic in case of API error"""
        # Basic keyword-based classification
        text_lower = text.lower()

        category = "information"
        priority = "medium"
        action_required = False

        # Simple keyword detection for English
        meeting_keywords = ["meeting", "call", "appointment", "schedule", "meet", "conference"]
        task_keywords = ["task", "todo", "deadline", "complete", "finish", "do", "need to", "must", "should"]
        thought_keywords = ["idea", "think", "suggest", "propose", "maybe", "consider", "what if"]

        if any(word in text_lower for word in meeting_keywords):
            category = "meeting"
            priority = "high"
            action_required = True
        elif any(word in text_lower for word in task_keywords):
            category = "task"
            priority = "high"
            action_required = True
        elif any(word in text_lower for word in thought_keywords):
            category = "thought"
            priority = "low"

        words = text.split()[:6]
        fallback_title = " ".join(words)
        if len(fallback_title) > 50:
            fallback_title = fallback_title[:47] + "..."

        return {
            "title": fallback_title or self._generate_fallback_title(category),
            "category": category,
            "confidence": 0.6,
            "entities": {
                "dates": [],
                "times": [],
                "contact": None,  # No contact extraction in fallback
                "projects": [],
                "keywords": text_lower.split()[:5]  # First 5 words as keywords
            },
            "priority": priority,
            "action_required": action_required,
            "summary": text[:100] + "..." if len(text) > 100 else text
        }


# Global classifier instance
message_classifier = MessageClassifier()
