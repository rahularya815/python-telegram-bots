# Telegram Channel Rating Bot 📊

A Python-based Telegram bot designed to automatically attach a rating system to every new post in a Telegram Channel. Users can vote on a scale of 1-10, and the bot calculates and displays the average rating in real-time.

## 🚀 Features

* **Auto-Detection:** Automatically detects new Text, Photo, Video, and Document posts in the channel.
* **Interactive Buttons:** Adds an inline keyboard with ratings from 1 to 10.
* **Real-Time Updates:** Calculates the average score immediately after a user votes.
* **Visual Progress Bar:** Displays a visual bar (e.g., `■■■■■■■□□□`) representing the score.
* **Spam Prevention:** Users can update their vote, but they cannot vote multiple times to manipulate the score.

## 🛠️ Tech Stack

* **Language:** Python 3.x
* **Library:** `python-telegram-bot` (v20+)
* **Deployment:** Compatible with Render (Background Worker), Heroku, or any VPS.

## 📂 Project Structure

```text
.
├── rating_bot.py       # The main bot source code
├── requirements.txt    # List of dependencies
├── README.md           # Documentation
└── .gitignore          # Files to ignore (e.g., local env files)
