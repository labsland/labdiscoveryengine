# Internationalization

So as to translate this project, you need to follow the following steps:

1. Extract the messages from the code repository into a file called 'messages.pot':
```
pybabel extract -F babel.cfg -k gettext -k lazy_gettext -k ng_gettext -o messages.pot --project labdiscoveryengine --version 0.1  .
```

2. Only if it is the first time that this language is used in LabDiscoveryEngine, initialize the language (being LANGUAGE 'uk', 'de' or 'es' or similar). This will create a folder in labdiscoveryengine/translations/LANGUAGE/LC_MESSAGES/messages.po:
```
pybabel init -i messages.pot -d labdiscoveryengine/translations -l LANGUAGE
```

3. Then update the translation files (this will update the messages.po file in the particular language):
```
pybabel update --no-fuzzy-matching -i messages.pot -d labdiscoveryengine/translations -l es
```

4. Then edit the .po file to write your own translations

5. Finally, run the following command to compile the translations:
```
pybabel compile -f -d labdiscoveryengine/translations
```
