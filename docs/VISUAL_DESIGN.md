# Studio visual design

The 2.0.1 presentation refresh keeps processing rules and project history unchanged. The original 2.0.0 download and benchmark evidence remain available.

The near-black canvas makes dense tables and light text easy to distinguish. Blue identifies actions; orange identifies the current step. Green, gold and rose messages retain separate success, warning and error meanings, with explanatory text. Table containers scroll independently on narrow screens.

The parsed-record breakdown uses the actual saved manifest: accepted, review and excluded populations appear as labeled segments. Their reveal and short screen transitions explain progress without counting up or changing final values. Unreadable-file populations remain explicitly unknown. Motion stops under the operating system's reduced-motion preference; no decorative loop or parallax is used.

The subsequent art-direction study adds a floating sidebar, layered source cards, a custom vector intake graphic and a donut displaying accepted share. The blue/apricot chart has its own labeled categorical legend; success, warning and error messages retain explicit text and distinct message colors. Accepted share is rule disposition, not a measure of factual accuracy. With no parsed rows, the center shows a dash and `NO PARSED ROWS` rather than a fabricated percentage. Rapid tab navigation resolves to the last requested step, and browsers without View Transitions use the ordinary tab behavior.

The separate motion-study page is a presentation of the actual app recording. Its frame entrance and chapter controls are presentation effects; they do not run on the working data table. The app itself remains a local preparation tool. Native workbook exports are static.

Palette values live in `web/design-tokens.json`. Run `python -B tools/build_theme.py` after changing them. This regenerates `web/theme-tokens.css`; `web/studio.css` controls the visual composition over the existing component structure. No external font download is needed.

Practice: change the `accent` color, rebuild the tokens and reload the local demo. Confirm that the selected step and project marker change, while review counts, numeric values and warning messages retain their meaning. Restore the chosen accent afterward.

The browser workflow check covers upload, corrections, relationship approval and downloads. `tools/capture_visual_pilot.py` captures actual fictional demo screens and checks narrow-screen page overflow; use scratch projects outside this repository. Historical benchmark evidence continues to identify the release originally tested.
