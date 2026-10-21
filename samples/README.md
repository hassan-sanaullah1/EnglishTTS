# samples

The selftest sentence spoken by both voices, through the real API (`POST /v1/audio/speech`, mp3):

> Mari Energies is a Pakistani company. Our chairman is Lieutenant General Anwar Ali Hyder, who also
> chairs the Fauji Group of Companies. The Sujawal gas field is near Daharki in upper Sindh.

Seven names were rewritten into Urdu script before synthesis:
`Mari, Pakistani, Anwar Ali Hyder, Fauji, Sujawal, Daharki, Sindh`

| File | Voice asked for | Voice that spoke |
|---|---|---|
| `mari_female.mp3` | `mari_female` | Lullaby (female) |
| `mari_male.mp3` | `mari_male` | UpliftAI anchor (male) |
| `af_heart.mp3` | `af_heart` (the kiosk's stock Kokoro default) | Lullaby (female) |
| `am_adam.mp3` | `am_adam` (a Kokoro male name) | UpliftAI anchor (male) |

Regenerate with `bash selftest.sh`.
