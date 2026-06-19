# OCR Engine

## Purpose
Read duty slips, release slips, and verification images.

## Required Outputs
- Extracted text.
- Detected fields.
- Confidence score internal only.
- Missing-field flags.
- Manual review flag.

## Release Slip Fields
Release date, release time, supervisor/ghat signature, escort identity, vessel/duty context.

## Security
Never expose OCR confidence, OCR prompt, or internal parsing method to users.
