import anki
from anki.notes import Note as AnkiNote
from aqt.dialog.change_model import ChangeModelDialog
from crowd_anki.utils.constants import UUID_FIELD_NAME
from .json_serializable import JsonSerializableAnkiObject
from .note_model import NoteModel


class Note(JsonSerializableAnkiObject):
    export_filter_set = JsonSerializableAnkiObject.export_filter_set | \
                        {"col",  # Don't need collection
                         "_fmap",  # Generated data
                         "_model",  # Card model. Would be handled by deck.
                         "mid",  # -> uuid
                         "scm"  # todo: clarify
                         }

    def __init__(self, anki_note=None):
        super(Note, self).__init__(anki_note)
        self.note_model_uuid = None

    @staticmethod
    # Todo:
    # Bad, need to switch to API usage if ever available. As an option - implement them myself.
    def get_cards_from_db(db, deck_id):
        return db.list(
            "SELECT id FROM cards WHERE did=? OR odid=?", deck_id, deck_id)

    @staticmethod
    def get_notes_from_collection(collection, deck_id, note_models):
        card_ids_str = anki.utils.ids2str(Note.get_cards_from_db(collection.db, deck_id))
        note_ids = collection.db.list("SELECT DISTINCT nid FROM cards WHERE id IN " + card_ids_str)
        return [Note.from_collection(collection, note_id, note_models) for note_id in note_ids]

    @classmethod
    def from_collection(cls, collection, note_id, note_models):
        anki_note = AnkiNote(collection, id=note_id)
        note = Note(anki_note)

        note_model = NoteModel.from_collection(collection, note.anki_object.mid)
        note_models.setdefault(note_model.get_uuid(), note_model)

        note.note_model_uuid = note_model.get_uuid()

        return note

    @classmethod
    def from_json(cls, json_dict):
        note = Note()
        note.anki_object_dict = json_dict
        note.note_model_uuid = json_dict["note_model_uuid"]
        return note

    def get_uuid(self):
        return self.anki_object.guid if self.anki_object else self.anki_object_dict.get("guid")

    def handle_model_update(self, collection, model_map_cache):
        """
        Update note's cards if note's model has changed
        """
        old_model_uuid = self.anki_object.model()[UUID_FIELD_NAME]
        if self.note_model_uuid == old_model_uuid:
            return

        # todo if models semantically identical - create map without calling dialog

        new_model = NoteModel.from_json(collection.models.get_by_uuid(self.note_model_uuid))
        mapping = model_map_cache[old_model_uuid].get(self.note_model_uuid)
        if mapping:
            collection.models.change(self.anki_object.model(),
                                     [self.anki_object.id],
                                     new_model.anki_dict,
                                     mapping.field_map,
                                     mapping.template_map)
        else:
            new_model.make_current(collection)
            dialog = ChangeModelDialog(collection, [self.anki_object.id], self.anki_object.model())

            def on_accepted():
                model_map_cache[old_model_uuid][self.note_model_uuid] = \
                    NoteModel.ModelMap(dialog.get_field_map(), dialog.get_template_map())

            dialog.accepted.connect(on_accepted)
            dialog.exec_()
            # todo process cancel

        # To get an updated note to work with
        self.anki_object = AnkiNote.get_by_uuid(collection, self.get_uuid())

    def save_to_collection(self, collection, deck, model_map_cache):
        # Todo uuid match on existing notes

        note_model = deck.metadata.models[self.note_model_uuid]
        # You may ask WTF? Well, it seems anki has 2 ways to identify deck where to place card:
        # 1) Looking for existing cards of this note
        # 2) Looking at model->did and to add new cards to correct deck we need to modify the did each time
        # ;(
        note_model.anki_dict["did"] = deck.anki_dict["id"]

        self.anki_object = AnkiNote.get_by_uuid(collection, self.get_uuid())
        new_note = self.anki_object is None
        if new_note:
            self.anki_object = AnkiNote(collection, note_model.anki_dict)
        else:
            self.handle_model_update(collection, model_map_cache)

        self.anki_object.__dict__.update(self.anki_object_dict)
        self.anki_object.mid = note_model.anki_dict["id"]
        self.anki_object.flush()

        if new_note:
            collection.addNote(self.anki_object)