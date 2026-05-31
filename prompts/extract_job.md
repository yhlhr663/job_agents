You are parsing a job posting. From the raw text below, return ONLY a JSON object (no prose, no code fences) with these keys:

- "title": job title (string)
- "company": hiring company name (string)
- "location": location as written, e.g. "New York, NY" or "Remote, US" (string)
- "is_us": true if the role can be performed in the United States (incl. US-remote), false if it is clearly outside the US, null if unclear
- "requires_login": true if applying clearly requires creating an account / logging in, else false
- "description": a clean plain-text version of the job description (responsibilities + requirements), max ~1500 words

If a field is unknown, use "" (or null for is_us).

RAW TEXT:
"""
{raw}
"""
