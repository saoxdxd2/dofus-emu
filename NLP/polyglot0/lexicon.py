# -*- coding: utf-8 -*-
"""Hand-authored lexicon for Polyglot-0.

Each row: (concept, pos, polarity, content, en, fr, es, de)
Forms are comma-separated. First form is the canonical translation.
This table is the human-written spec from which all weights are programmed.
No training data and no gradients are used.
"""

CONCEPTS = [
    # pronouns
    ("i", "pron", 0.0, True, "i", "je,j',moi", "yo,me", "ich,mich"),
    ("you", "pron", 0.0, True, "you", "tu,toi,vous", "usted,ustedes", "du,dich"),
    ("he", "pron", 0.0, True, "he,him", "il,lui", "él", "er,ihn"),
    ("she", "pron", 0.0, True, "she,her", "elle", "ella", "sie"),
    ("we", "pron", 0.0, True, "we,us", "nous", "nosotros,nosotras", "wir,uns"),
    ("they", "pron", 0.0, True, "they,them", "ils,elles", "ellos,ellas", ""),

    # nouns
    ("cat", "noun", 0.0, True, "cat,cats", "chat,chats", "gato,gatos", "Katze,Katzen"),
    ("dog", "noun", 0.0, True, "dog,dogs", "chien,chiens", "perro,perros", "Hund,Hunde"),
    ("coffee", "noun", 0.0, True, "coffee", "café,cafés", "café,cafés", "Kaffee"),
    ("tea", "noun", 0.0, True, "tea", "thé", "té", "Tee"),
    ("bread", "noun", 0.0, True, "bread", "pain", "pan", "Brot"),
    ("cheese", "noun", 0.0, True, "cheese", "fromage", "queso", "Käse"),
    ("apple", "noun", 0.0, True, "apple,apples", "pomme,pommes", "manzana,manzanas", "Apfel,Äpfel"),
    ("water", "noun", 0.0, True, "water", "eau,eaux", "agua,aguas", "Wasser"),
    ("wine", "noun", 0.0, True, "wine", "vin", "vino", "Wein"),
    ("fish", "noun", 0.0, True, "fish", "poisson", "pescado,pescados", "Fisch"),
    ("house", "noun", 0.0, True, "house,houses", "maison,maisons", "casa,casas", "Haus,Häuser"),
    ("car", "noun", 0.0, True, "car,cars", "voiture,voitures", "coche,coches", "Auto,Autos"),
    ("book", "noun", 0.0, True, "book,books", "livre,livres", "libro,libros", "Buch,Bücher"),
    ("film", "noun", 0.0, True, "film,films", "film,films", "película,películas", "Film,Filme"),
    ("music", "noun", 0.0, True, "music", "musique", "música", "Musik"),
    ("city", "noun", 0.0, True, "city,cities", "ville,villes", "ciudad,ciudades", "Stadt,Städte"),
    ("friend", "noun", 0.0, True, "friend,friends", "ami,amis,amie,amies", "amigo,amigos,amiga,amigas", "Freund,Freunde"),
    ("day", "noun", 0.0, True, "day,days", "jour,jours", "día,días", "Tag,Tage"),
    ("weather", "noun", 0.0, True, "weather", "temps", "tiempo", "Wetter"),
    ("food", "noun", 0.0, True, "food", "nourriture", "comida", "Nahrung"),
    ("school", "noun", 0.0, True, "school", "école", "escuela", "Schule"),

    # verbs
    ("like", "verb", 0.6, True, "like,likes,liked", "aime,aimes,aimons,aimez,aiment,aimé", "gusta,gustan,gustó,gustar", "mag,magst,mögen,mochte"),
    ("love", "verb", 1.0, True, "love,loves,loved", "adore,adores,adorons,adorez,adorent", "encanta,encantan,encantar,amo,ama", "liebe,liebst,lieben,liebte"),
    ("hate", "verb", -1.0, True, "hate,hates,hated", "déteste,détestes,détestent", "odia,odian,odiar", "hasse,hasst,hassen"),
    ("eat", "verb", 0.0, True, "eat,eats,ate,eaten", "mange,manges,mangeons,mangez,mangent", "come,comes,comemos,comen", "esse,isst,essen,aß"),
    ("drink", "verb", 0.0, True, "drink,drinks,drank,drunk", "bois,boit,buvons,buvez,boivent", "bebe,bebes,bebemos,beben", "trinke,trinkst,trinkt,trinken"),
    ("see", "verb", 0.0, True, "see,sees,saw,seen", "vois,voit,voyons,voyez,voient", "veo,ves,ve,vemos,ven", "sehe,siehst,sieht,sehen"),
    ("want", "verb", 0.0, True, "want,wants,wanted", "veux,veut,voulons,voulez,veulent", "quiero,quieres,quiere,queremos,quieren", "will,wollen,wollte"),
    ("buy", "verb", 0.0, True, "buy,buys,bought", "achète,achètes,achetons,achetez,achètent", "compro,compras,compra,compramos,compran", "kaufe,kaufst,kauft,kaufen"),
    ("read", "verb", 0.0, True, "read,reads", "lis,lit,lisons,lisez,lisent", "leo,lees,lee,leemos,leen", "lese,liest,lesen"),
    ("live", "verb", 0.0, True, "live,lives,lived", "vis,vit,vivons,vivez,vivent", "vivo,vives,vive,vivimos,viven", "lebe,lebst,lebt,leben"),
    ("speak", "verb", 0.0, True, "speak,speaks,spoke,spoken", "parle,parles,parlons,parlez,parlent", "hablo,hablas,habla,hablamos,hablan", "spreche,sprichst,spricht,sprechen"),
    ("find", "verb", 0.0, True, "find,finds,found", "trouve,trouves,trouvons,trouvez,trouvent", "encuentro,encuentras,encuentra,encontramos,encuentran", "finde,findest,findet,finden"),
    ("work", "verb", 0.0, True, "work,works,worked", "travaille,travailles,travaillons,travaillez,travaillent", "trabajo,trabajas,trabaja,trabajamos,trabajan", "arbeite,arbeitest,arbeitet,arbeiten"),

    # adjectives
    ("good", "adj", 1.0, True, "good", "bon,bonne,bons,bonnes", "bueno,buena,buenos,buenas", "gut,gute,guten,guter,gutes"),
    ("bad", "adj", -1.0, True, "bad", "mauvais,mauvaise,mauvaises", "malo,mala,malos,malas", "schlecht,schlechte,schlechten,schlechter"),
    ("beautiful", "adj", 0.8, True, "beautiful", "beau,belle,beaux,belles", "hermoso,hermosa,hermosos,hermosas", "schön,schöne,schönen,schöner"),
    ("ugly", "adj", -0.8, True, "ugly", "laid,laide,laids,laides", "feo,fea,feos,feas", "hässlich,hässliche,hässlichen"),
    ("happy", "adj", 1.0, True, "happy", "heureux,heureuse,heureuses", "feliz,felices", "glücklich,glückliche,glücklichen"),
    ("sad", "adj", -1.0, True, "sad", "triste,tristes", "triste,tristes", "traurig,traurige,traurigen"),
    ("big", "adj", 0.0, True, "big", "grand,grande,grands,grandes", "grande,grandes", "groß,große,großen"),
    ("small", "adj", 0.0, True, "small", "petit,petite,petits,petites", "pequeño,pequeña,pequeños,pequeñas", "klein,kleine,kleinen,kleines"),
    ("hot", "adj", 0.3, True, "hot", "chaud,chaude,chauds", "caliente,calientes", "heiß,heiße,heißen"),
    ("cold", "adj", -0.4, True, "cold", "froid,froide,froids", "frío,fría,fríos", "kalt,kalte,kalten"),
    ("delicious", "adj", 1.0, True, "delicious", "délicieux,délicieuse", "delicioso,deliciosa", "lecker,leckeres"),
    ("awful", "adj", -1.0, True, "awful", "horrible,horribles", "horrible,horribles", "schrecklich,schreckliche"),
    ("expensive", "adj", -0.3, True, "expensive", "cher,chère", "caro,caros", "teuer,teure"),
    ("cheap", "adj", 0.3, True, "cheap", "économique,économiques", "barato,barata", "billig,billige"),
    ("new", "adj", 0.0, True, "new", "nouveau,nouvelle,nouveaux", "nuevo,nueva", "neu,neue,neues"),
    ("old", "adj", 0.0, True, "old", "vieux,vieille", "viejo,vieja", "alt,alte,alten"),
    ("fast", "adj", 0.5, True, "fast", "rapide,rapides", "rápido,rápida", "schnell,schnelle"),
    ("slow", "adj", -0.5, True, "slow", "lent,lente", "lento,lenta", "langsam,langsame"),
    ("easy", "adj", 0.5, True, "easy", "facile,faciles", "fácil,fáciles", "einfach,einfache"),
    ("difficult", "adj", -0.5, True, "difficult", "difficile,difficiles", "difícil,difíciles", "schwierig,schwierige"),
    ("interesting", "adj", 0.8, True, "interesting", "intéressant,intéressante", "interesante,interesantes", "interessant,interessante"),
    ("boring", "adj", -0.8, True, "boring", "ennuyeux,ennuyeuse", "aburrido,aburrida", "langweilig,langweilige"),

    # adverbs
    ("today", "adv", 0.0, True, "today", "aujourd'hui", "hoy", "heute"),
    ("here", "adv", 0.0, True, "here", "ici", "aquí", "hier"),
    ("there", "adv", 0.0, True, "there", "là", "allí,ahí", "dort"),
    ("much", "adv", 0.0, True, "much", "beaucoup", "mucho", "viel"),
    ("little", "adv", 0.0, True, "little", "peu", "poco", "wenig"),
    ("always", "adv", 0.0, True, "always", "toujours", "siempre", "immer"),
    ("really", "adv", 0.0, False, "really", "vraiment", "realmente", "wirklich"),

    # function words
    ("the", "det", 0.0, False, "the", "le,la,les,l',des", "el,la,los,las", "der,die,das,den,dem,des"),
    ("a", "det", 0.0, False, "a,an", "un,une", "un,una,unos,unas", "ein,eine,einen,einem,eines"),
    ("and", "conj", 0.0, False, "and", "et", "y,e", "und"),
    ("but", "conj", 0.0, False, "but", "mais", "pero,sino", "aber"),
    ("in", "prep", 0.0, False, "in", "dans,en", "en", "in,im"),
    ("with", "prep", 0.0, False, "with", "avec", "con", "mit"),
    ("of", "prep", 0.0, False, "of", "de,d'", "de", "von"),
    ("to", "prep", 0.0, False, "to", "à,au,aux", "al", "zu,zum,zur"),
    ("for", "prep", 0.0, False, "for", "pour", "para,por", "für"),
    ("on", "prep", 0.0, False, "on", "sur", "sobre", "auf"),
    ("or", "conj", 0.0, False, "or", "ou", "o", "oder"),
    ("my", "det", 0.0, False, "my", "mon,ma,mes", "mi,mis", "mein,meine,meinen,meinem"),
    ("your", "det", 0.0, False, "your", "ton,ta,tes", "su,sus", "dein,deine,deinen,deinem"),
    ("this", "det", 0.0, False, "this", "ce,cette,ces", "este,esta,estos,estas", "dieser,diese,dieses,diesen"),
    ("that", "det", 0.0, False, "that", "cela,ça", "ese,esa,eso", "jener,jene,jenes"),
    ("be", "verb", 0.0, False, "is,be,am,are,was,were,been,being", "est,être,sont,suis,es,était,étaient", "es,ser,son,soy,era,está,están", "ist,sein,sind,bin,bist,war,waren"),
    ("have", "verb", 0.0, False, "have,has,had,having", "avoir,ai,as,avons,avez,ont,avait", "tener,tengo,tiene,tenemos,tienen,tenía", "haben,habe,hast,hat,hatten,hatte"),
    ("go", "verb", 0.0, False, "go,goes,went,gone", "aller,vais,va,allons,allez,vont", "ir,voy,va,vamos,van", "gehen,gehe,geht,ging"),
    ("aux", "aux", 0.0, False, "do,does,did,can,could,should", "", "", ""),
    ("not", "neg", 0.0, False, "not,n't,never", "pas,ne,n',jamais", "no,nunca", "nicht,nie,kein,keine"),
    ("very", "int", 0.0, False, "very,so,too", "très,trop", "muy,demasiado", "sehr"),
    ("yes", "interj", 0.0, False, "yes", "oui", "sí", "ja"),
]

LANG_INDEX = {"en": 0, "fr": 1, "es": 2, "de": 3}
LANGS = ["en", "fr", "es", "de"]

CORPUS = [
    dict(id="s01", concepts=["i", "like", "good", "coffee"], y="pos", text={
        "en": "I like the good coffee.",
        "fr": "J'aime le bon café.",
        "es": "Me gusta el café bueno.",
        "de": "Ich mag den guten Kaffee."}),
    dict(id="s02", concepts=["cat", "beautiful"], y="pos", text={
        "en": "The cat is very beautiful.",
        "fr": "Le chat est très beau.",
        "es": "El gato es muy hermoso.",
        "de": "Die Katze ist sehr schön."}),
    dict(id="s03", concepts=["dog", "happy"], y="neg", text={
        "en": "The dog is not happy.",
        "fr": "Le chien n'est pas heureux.",
        "es": "El perro no es feliz.",
        "de": "Der Hund ist nicht glücklich."}),
    dict(id="s04", concepts=["we", "eat", "bread", "cheese"], y="neu", text={
        "en": "We eat the bread and the cheese.",
        "fr": "Nous mangeons le pain et le fromage.",
        "es": "Nosotros comemos el pan y el queso.",
        "de": "Wir essen das Brot und den Käse."}),
    dict(id="s05", concepts=["she", "drink", "tea", "city"], y="neu", text={
        "en": "She drinks the tea in the city.",
        "fr": "Elle boit le thé dans la ville.",
        "es": "Ella bebe el té en la ciudad.",
        "de": "Sie trinkt den Tee in der Stadt."}),
    dict(id="s06", concepts=["we", "want", "small", "house"], y="neu", text={
        "en": "We want a small house.",
        "fr": "Nous voulons une petite maison.",
        "es": "Nosotros queremos una casa pequeña.",
        "de": "Wir wollen ein kleines Haus."}),
    dict(id="s07", concepts=["film", "awful"], y="neg", text={
        "en": "The film is awful.",
        "fr": "Le film est horrible.",
        "es": "La película es horrible.",
        "de": "Der Film ist schrecklich."}),
    dict(id="s08", concepts=["weather", "cold", "today"], y="neg", text={
        "en": "The weather is cold today.",
        "fr": "Le temps est froid aujourd'hui.",
        "es": "El tiempo está frío hoy.",
        "de": "Das Wetter ist heute kalt."}),
    dict(id="s09", concepts=["i", "love", "beautiful", "music"], y="pos", text={
        "en": "I love the beautiful music.",
        "fr": "J'adore la belle musique.",
        "es": "Me encanta la música hermosa.",
        "de": "Ich liebe die schöne Musik."}),
    dict(id="s10", concepts=["he", "hate", "ugly", "car"], y="neg", text={
        "en": "He hates the ugly car.",
        "fr": "Il déteste la laide voiture.",
        "es": "Él odia el coche feo.",
        "de": "Er hasst das hässliche Auto."}),
    dict(id="s11", concepts=["bread", "delicious"], y="pos", text={
        "en": "The bread is delicious.",
        "fr": "Le pain est délicieux.",
        "es": "El pan es delicioso.",
        "de": "Das Brot ist lecker."}),
    dict(id="s12", concepts=["wine", "good"], y="neg", text={
        "en": "The wine is not good.",
        "fr": "Le vin n'est pas bon.",
        "es": "El vino no es bueno.",
        "de": "Der Wein ist nicht gut."}),
    dict(id="s13", concepts=["friend", "happy"], y="pos", text={
        "en": "My friend is happy.",
        "fr": "Mon ami est heureux.",
        "es": "Mi amigo está feliz.",
        "de": "Mein Freund ist glücklich."}),
    dict(id="s14", concepts=["house", "big"], y="neu", text={
        "en": "Your house is big.",
        "fr": "Ta maison est grande.",
        "es": "Su casa es grande.",
        "de": "Dein Haus ist groß."}),
    dict(id="s15", concepts=["book", "interesting"], y="pos", text={
        "en": "This book is interesting.",
        "fr": "Ce livre est intéressant.",
        "es": "Este libro es interesante.",
        "de": "Dieses Buch ist interessant."}),
    dict(id="s16", concepts=["film", "boring"], y="neg", text={
        "en": "That film is boring.",
        "fr": "Ce film est ennuyeux.",
        "es": "Esa película es aburrida.",
        "de": "Dieser Film ist langweilig."}),
    dict(id="s17", concepts=["water", "cold"], y="neg", text={
        "en": "The water is very cold.",
        "fr": "L'eau est très froide.",
        "es": "El agua está muy fría.",
        "de": "Das Wasser ist sehr kalt."}),
    dict(id="s18", concepts=["i", "want", "new", "car"], y="neu", text={
        "en": "I want a new car.",
        "fr": "Je veux une nouvelle voiture.",
        "es": "Yo quiero un coche nuevo.",
        "de": "Ich will ein neues Auto."}),
    dict(id="s19", concepts=["we", "see", "old", "house"], y="neu", text={
        "en": "We see the old house.",
        "fr": "Nous voyons la vieille maison.",
        "es": "Nosotros vemos la casa vieja.",
        "de": "Wir sehen das alte Haus."}),
    dict(id="s20", concepts=["cheese", "good", "bread", "delicious"], y="pos", text={
        "en": "The cheese is good and the bread is delicious.",
        "fr": "Le fromage est bon et le pain est délicieux.",
        "es": "El queso es bueno y el pan es delicioso.",
        "de": "Der Käse ist gut und das Brot ist lecker."}),
    dict(id="s21", concepts=["fish", "expensive", "delicious"], y="pos", text={
        "en": "The fish is expensive but delicious.",
        "fr": "Le poisson est cher mais délicieux.",
        "es": "El pescado es caro pero delicioso.",
        "de": "Der Fisch ist teuer aber lecker."}),
    dict(id="s22", concepts=["small", "dog", "happy"], y="pos", text={
        "en": "The small dog is very happy.",
        "fr": "Le petit chien est très heureux.",
        "es": "El perro pequeño está muy feliz.",
        "de": "Der kleine Hund ist sehr glücklich."}),
    dict(id="s23", concepts=["old", "car", "fast"], y="neg", text={
        "en": "The old car is not fast.",
        "fr": "La vieille voiture n'est pas rapide.",
        "es": "El coche viejo no es rápido.",
        "de": "Das alte Auto ist nicht schnell."}),
    dict(id="s24", concepts=["new", "film", "interesting"], y="pos", text={
        "en": "The new film is really interesting.",
        "fr": "Le nouveau film est vraiment intéressant.",
        "es": "La nueva película es realmente interesante.",
        "de": "Der neue Film ist wirklich interessant."}),
    dict(id="s25", concepts=["tea", "hot", "delicious"], y="pos", text={
        "en": "The tea is hot and delicious.",
        "fr": "Le thé est chaud et délicieux.",
        "es": "El té está caliente y delicioso.",
        "de": "Der Tee ist heiß und lecker."}),
    dict(id="s26", concepts=["city", "big", "beautiful"], y="pos", text={
        "en": "The city is big but beautiful.",
        "fr": "La ville est grande mais belle.",
        "es": "La ciudad es grande pero hermosa.",
        "de": "Die Stadt ist groß aber schön."}),
    dict(id="s27", concepts=["coffee", "bad"], y="pos", text={
        "en": "The coffee is not bad.",
        "fr": "Le café n'est pas mauvais.",
        "es": "El café no es malo.",
        "de": "Der Kaffee ist nicht schlecht."}),
    dict(id="s28", concepts=["cat", "beautiful"], y="neg", text={
        "en": "The cat is not beautiful.",
        "fr": "Le chat n'est pas beau.",
        "es": "El gato no es hermoso.",
        "de": "Die Katze ist nicht schön."}),
    dict(id="s29", concepts=["she", "drink", "wine"], y="neu", text={
        "en": "She never drinks the wine.",
        "fr": "Elle ne boit jamais le vin.",
        "es": "Ella nunca bebe el vino.",
        "de": "Sie trinkt nie den Wein."}),
    dict(id="s30", concepts=["music", "boring"], y="pos", text={
        "en": "The music is not boring.",
        "fr": "La musique n'est pas ennuyeuse.",
        "es": "La música no es aburrida.",
        "de": "Die Musik ist nicht langweilig."}),
]

# Extra surface forms and elisions. They map to an existing concept.
EXTRA_FORMS = {
    "en": {
        "doesn't": "not", "don't": "not", "isn't": "not", "aren't": "not",
        "wasn't": "not", "weren't": "not", "can't": "not", "won't": "not",
    },
    "fr": {
        "n'": "not", "j'": "i", "l'": "the", "d'": "of", "qu'": "that",
        "c'": "this", "s'": "be", "t'": "you", "m'": "i",
    },
    "es": {},
    "de": {
        "im": "in", "zum": "to", "zur": "to",
    },
}
