# Sources for Sweden

| Source | What is read | Past versions | Names the amending act | Terms |
|---|---|---|---|---|
| [Riksdagens öppna data](https://data.riksdagen.se) (`dokumentlista`, `dokumentstatus/<dok_id>.json`) | the list of SFS documents and each statute's consolidated text (SFST, Regeringskansliet), HTML and plain text, with Utfärdad, Ändrad t.o.m., Upphävd and the repealing act | no: one document per statute, the current consolidation | per paragraf ("Lag (2026:1604).") | "fria att användas och spridas vidare så länge du anger källa" (Källa: Sveriges riksdag); not the Riksdag's name or logo for the service |
| [Regeringskansliets rättsdatabaser, SFSR](https://rkrattsbaser.gov.se/sfsr) (`sfsr?bet=<SFS number>`) | the register of amendments: every amending SFS number, its scope (Omfattning) and in-force day (Ikraft) | n/a | yes | no terms page published |
| [Regeringskansliets rättsdatabaser, SFST](https://rkrattsbaser.gov.se/sfst) (`sfst?bet=<SFS number>`) | the text of the 6 statutes whose Riksdagen document answers 404 (old "bih." numbers) | no | n/a | no terms page published |

robots.txt: data.riksdagen.se and rkrattsbaser.gov.se have none (HTTP 404); www.riksdagen.se, which is not fetched,
names Claudebot, Claude-User and Claude-SearchBot only to set `crawl-delay:1`. Retrieved 2026-10-28; 23898
requests in all, at most 4 at a time.

## Legal basis

Swedish statutes carry no copyright: upphovsrättslagen (1960:729) 1 kap. 9 § (`1960/729/kap-1/9.md` here):
"9 § Upphovsrätt gäller inte till 1. författningar, 2. beslut av myndigheter, 3. yttranden av svenska myndigheter och 4. officiella översättningar av sådant som avses i 1-3. Upphovsrätt gäller dock till verk vilka ingår i en handling som avses i första stycket och är av följande slag: 1. kartor, 2. alster av bildkonst, 3. musikaliska verk eller 4. diktverk. Upphovsrätt gäller även till ett verk som ingår i en bilaga till ett beslut av en myndighet, om beslutet avser rätten att ta del av den allmänna handling där verket ingår. Lag (2000:92)."

The text is copied unchanged, only split into files and lines. This service is not provided or endorsed by Sveriges
riksdag or Regeringskansliet. The authoritative text is the one published in Svensk författningssamling
(svenskforfattningssamling.se).
