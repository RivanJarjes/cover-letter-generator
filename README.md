# Cover Letter Generator

An AI-powered desktop application that generates tailored cover letters using your resume and job descriptions.

## Demo

https://github.com/user-attachments/assets/bc143b68-27de-43be-b2b0-65da1327731e

## How to Run

### Prerequisites
- Python 3.8 or higher
- OpenAI API key

### Installation

1. Clone the repository:
```bash
git clone https://github.com/RivanJarjes/cover-letter-generator.git
cd cover-letter-generator
```

2. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On macOS/Linux
# or
.venv\Scripts\activate  # On Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up your OpenAI API key:
Create a `.env` file in the project root:
```
OPENAI_API_KEY=your_api_key_here
```

### Running the Application

From the project root directory:

```bash
python -m src.main
```

Or using the virtual environment directly:
```bash
.venv/bin/python -m src.main
```

### Usage

1. **Upload your resume** - Click "Upload Resume" and select your resume file (PDF or text)
2. **Optionally upload a sample cover letter** - Click "Upload Sample (Optional)" for stylistic reference
3. **Paste a job description** - Press `Cmd+V` (macOS) or `Ctrl+V` (Windows/Linux) to paste the job description from your clipboard
4. **View the generated cover letter** - The PDF will be automatically generated and saved to your configured output directory
