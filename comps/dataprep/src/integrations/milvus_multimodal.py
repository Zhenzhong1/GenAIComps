# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# for test

import base64
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Type, Union

import pymupdf
from fastapi import Body, File, Form, HTTPException, UploadFile
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.utils import get_from_dict_or_env
from langchain_milvus.vectorstores import Milvus
from PIL import Image

from comps import CustomLogger, DocPath, OpeaComponent, OpeaComponentRegistry, ServiceType
from comps.third_parties.bridgetower.src.bridgetower_embedding import BridgeTowerEmbedding

from .utils.multimodal import (
    clear_upload_folder,
    convert_video_to_audio,
    create_upload_folder,
    delete_audio_file,
    extract_frames_and_annotations_from_transcripts,
    extract_frames_and_generate_captions,
    extract_transcript_from_audio,
    generate_annotations_from_transcript,
    generate_id,
    load_json_file,
    load_whisper_model,
    resize_image,
    write_vtt,
)

logger = CustomLogger("opea_dataprep_milvus_multimodal")
logflag = os.getenv("LOGFLAG", False)
partition_field_name = "filename"
upload_folder = "./uploaded_files/"

# Models
EMBED_MODEL = os.getenv("EMBEDDING_MODEL_ID", "BridgeTower/bridgetower-large-itm-mlm-itc")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

# Lvm Microservice Information
LVM_ENDPOINT = os.getenv("LVM_ENDPOINT", "http://localhost:9399/v1/lvm")

# MILVUS configuration
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", 19530))
MILVUS_URI = f"http://{MILVUS_HOST}:{MILVUS_PORT}"
INDEX_PARAMS = {"index_type": "COMPOSITE", "metric_type": "IP", "params": {}}
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "mm_rag_milvus")

MILVUS_CONNECTION = {
    "uri": MILVUS_URI,
}


class MultimodalMilvus(Milvus):
    """Milvus vector database to process multimodal data."""

    @classmethod
    def from_text_image_pairs_return_keys(
        cls: Type[Milvus],
        texts: List[str],
        embedding: Embeddings = BridgeTowerEmbedding,
        images: List[str] = None,
        metadatas: Optional[List[dict]] = None,
        collection_name: Optional[str] = None,
        vector_schema: Optional[Dict[str, Union[str, int]]] = None,
        **kwargs: Any,
    ):
        """
        Args:
            texts (List[str]): List of texts to add to the vectorstore.
            images (List[str]): Optional list of path-to-images to add to the vectorstore. If provided, the length of
                the list of images must match the length of the list of text strings.
            metadatas (Optional[List[dict]], optional): Optional list of metadata
                dicts to add to the vectorstore. Defaults to None.
            collection_name (Optional[str], optional): Optional name of the index to
                create or add to. Defaults to None.
            vector_schema (Optional[Dict[str, Union[str, int]]], optional): Optional
                vector schema to use. Defaults to None.
            **kwargs (Any): Additional keyword arguments to pass to the Milvus client.
        Returns:
            Tuple[Milvus, List[str]]: Tuple of the Milvus instance and the keys of
                the newly created documents.
        Raises:
            ValueError: If the number of texts does not equal the number of images.
            ValueError: If the number of metadatas does not match the number of texts.
        """
        # If images are provided, the length of texts must be equal to the length of images
        if images and len(texts) != len(images):
            raise ValueError(f"the len of captions {len(texts)} does not equal the len of images {len(images)}")

        milvus_uri = get_from_dict_or_env(kwargs, "milvus_uri", "MILVUS_URI")

        if "milvus_uri" in kwargs:
            kwargs.pop("milvus_uri")

        # flag to use generated schema
        if "generate" in kwargs:
            kwargs.pop("generate")

        # see if the user specified keys
        keys = None
        if "keys" in kwargs:
            keys = kwargs.pop("keys")

        filename = ""
        if "filename" in kwargs:
            filename = kwargs.pop("filename")

        # Name of the search index if not given
        if not collection_name:
            collection_name = uuid.uuid4().hex

        # type check for metadata
        if metadatas:
            if isinstance(metadatas, list) and len(metadatas) != len(texts):  # type: ignore
                raise ValueError("Number of metadatas must match number of texts")
            if not (isinstance(metadatas, list) and isinstance(metadatas[0], dict)):
                raise ValueError("Metadatas must be a list of dicts")
            # generated_schema = _prepare_metadata_fields(metadatas[0])
        # Create instance
        metadatas_test = metadatas
        instance = cls(
            collection_name,
            vector_schema=vector_schema,
            connection_args=MILVUS_CONNECTION,
            auto_id=True,
            partition_key_field=partition_field_name,
            **kwargs,
        )
        keys = (
            instance.add_text_image_pairs(texts, images, filename, embedding, metadatas=metadatas_test, keys=keys)
            if images
            else instance.add_text(texts, filename, metadatas=metadatas_test, keys=keys)
        )
        return instance, keys

    def add_text_image_pairs(
        self,
        texts: Iterable[str],
        images: Iterable[str],
        filename: str,
        embedding: Embeddings = BridgeTowerEmbedding,
        metadatas: Optional[List[dict]] = None,
        embeddings: Optional[List[List[float]]] = None,
        batch_size: int = 2,
        clean_metadata: bool = True,
        **kwargs: Any,
    ) -> List[str]:
        ids = []
        # Get keys or ids from kwargs
        # Other vectorstores use ids
        keys_or_ids = kwargs.get("keys", kwargs.get("ids"))
        if metadatas:
            if isinstance(metadatas, list) and len(metadatas) != len(texts):  # type: ignore
                raise ValueError("Number of metadatas must match number of texts")
            if not (isinstance(metadatas, list) and isinstance(metadatas[0], dict)):
                raise ValueError("Metadatas must be a list of dicts")

        pil_imgs = [Image.open(img) for img in images]
        if not embeddings:
            embeddings = embedding.embed_image_text_pairs(list(texts), pil_imgs, batch_size=batch_size)
        for metadata in metadatas:
            metadata["filename"] = filename
        db_ids = self.add_embeddings(list(texts), embeddings, metadatas, batch_size)
        logger.info(db_ids)
        return db_ids

    def add_text(
        self,
        texts: Iterable[str],
        filename: str,
        embedding: Embeddings = BridgeTowerEmbedding,
        metadatas: Optional[List[dict]] = None,
        embeddings: Optional[List[List[float]]] = None,
        clean_metadata: bool = True,
        **kwargs: Any,
    ) -> List[str]:

        ids = []
        # Get keys or ids from kwargs
        # Other vectorstores use ids
        keys_or_ids = kwargs.get("keys", kwargs.get("ids"))

        # type check for metadata
        if metadatas:
            if isinstance(metadatas, list) and len(metadatas) != len(texts):  # type: ignore
                raise ValueError("Number of metadatas must match number of texts")
            if not (isinstance(metadatas, list) and isinstance(metadatas[0], dict)):
                raise ValueError("Metadatas must be a list of dicts")

        if not embeddings:
            embedding = BridgeTowerEmbedding()
            embeddings = embedding.embed_documents(texts=list(texts))

        batch_size = 2
        for metadata in metadatas:
            metadata["filename"] = filename

        db_ids = self.add_embeddings(list(texts), embeddings, metadatas, batch_size)
        logger.info(db_ids)
        return db_ids


def search_by_file(collection, file_name):
    query = f"{partition_field_name} == '{file_name}'"
    results = collection.query(
        expr=query,
        output_fields=[partition_field_name, "pk"],
    )
    if logflag:
        logger.info(f"[ search by file ] searched by {file_name}")
        logger.info(f"[ search by file ] {len(results)} results: {results}")
    return results


def search_all(collection):
    results = collection.query(expr="pk >= 0", output_fields=[partition_field_name, "pk"])
    if logflag:
        logger.info(f"[ search all ] {len(results)} results: {results}")
    return results


def delete_all_data(my_milvus):
    if logflag:
        logger.info("[ delete all ] deleting all data in milvus")
    if my_milvus.col:
        my_milvus.col.drop()
        if logflag:
            logger.info("[ delete all ] delete success: all data")


def delete_by_partition_field(my_milvus, partition_field):
    if logflag:
        logger.info(f"[ delete partition ] deleting {partition_field_name} {partition_field}")
    pks = my_milvus.get_pks(f'{partition_field_name} == "{partition_field}"')
    if logflag:
        logger.info(f"[ delete partition ] target pks: {pks}")
    res = my_milvus.delete(pks)
    my_milvus.col.flush()
    if logflag:
        logger.info(f"[ delete partition ] delete success: {res}")


@OpeaComponentRegistry.register("OPEA_DATAPREP_MULTIMODALMILVUS")
class OpeaMultimodalMilvusDataprep(OpeaComponent):
    """A specialized dataprep component derived from OpeaComponent for milvus dataprep services.
    Dataprep component for Multimodal Milvus ingestion and search services.

    Attributes:
        client (Milvus): An instance of the milvus client for vector database operations.
    """

    def __init__(self, name: str, description: str, config: dict = None):
        super().__init__(name, ServiceType.DATAPREP.name.lower(), description, config)
        self.device = "cpu"
        # Load embeddings model
        logger.info("Initializing BridgeTower model as embedder...")
        self.upload_folder = "./uploaded_files/"
        self.embeddings = BridgeTowerEmbedding(model_name=EMBED_MODEL, device=self.device)
        logger.info("Done initialization of embedder!")

        health_status = self.check_health()
        if not health_status:
            logger.error("OpeaMilvusDataprep health check failed.")

    def check_health(self) -> bool:
        if logflag:
            logger.info("[ health check ] start to check health of milvus")
        try:
            if self.embeddings is None:
                logger.error("Multimodal Milvus is not initialized.")
                return False

            client = Milvus(
                embedding_function=self.embeddings,
                collection_name=COLLECTION_NAME,
                connection_args={"uri": MILVUS_URI},
                index_params=INDEX_PARAMS,
                auto_id=True,
            )
            _ = client.client.list_collections()
            if logflag:
                logger.info("[ health check ] Successfully connected to Milvus!")
            return True
        except Exception as e:
            logger.info(f"[ health check ] Failed to connect to Milvus: {e}")
            return False

    def invoke(self, *args, **kwargs):
        pass

    def prepare_data_and_metadata_from_annotation(
        self,
        annotation,
        path_to_frames,
        title,
        num_transcript_concat_for_ingesting=2,
        num_transcript_concat_for_inference=7,
    ):
        text_list = []
        image_list = []
        metadatas = []
        for i, frame in enumerate(annotation):
            frame_index = frame["sub_video_id"]
            path_to_frame = os.path.join(path_to_frames, f"frame_{frame_index}.png")
            # augment this frame's transcript with a reasonable number of neighboring frames' transcripts helps semantic retrieval
            lb_ingesting = max(0, i - num_transcript_concat_for_ingesting)
            ub_ingesting = min(len(annotation), i + num_transcript_concat_for_ingesting + 1)
            caption_for_ingesting = " ".join([annotation[j]["caption"] for j in range(lb_ingesting, ub_ingesting)])

            # augment this frame's transcript with more neighboring frames' transcript to provide more context to LVM for question answering
            lb_inference = max(0, i - num_transcript_concat_for_inference)
            ub_inference = min(len(annotation), i + num_transcript_concat_for_inference + 1)
            caption_for_inference = " ".join([annotation[j]["caption"] for j in range(lb_inference, ub_inference)])

            video_id = frame["video_id"]
            b64_img_str = frame["b64_img_str"]
            time_of_frame = frame["time"]
            embedding_type = "pair" if b64_img_str else "text"
            source_video = frame["video_name"]

            text_list.append(caption_for_ingesting)

            if b64_img_str:
                image_list.append(path_to_frame)

            metadatas.append(
                {
                    "content": caption_for_ingesting,
                    "b64_img_str": b64_img_str,
                    "video_id": video_id,
                    "source_video": source_video,
                    "time_of_frame_ms": float(time_of_frame),
                    "embedding_type": embedding_type,
                    "title": title,
                    "transcript_for_inference": caption_for_inference,
                }
            )

        return text_list, image_list, metadatas

    def prepare_pdf_data_from_annotation(self, annotation, path_to_files, title):
        """PDF data processing has some key differences from videos and images.

        1. Neighboring transcripts are not currently considered relevant.
        We are only taking the text located on the same page as the image.
        2. The images within PDFs are indexed by page and image-within-page
        indices, as opposed to a single frame index.
        3. Instead of time of frame in ms, we return the PDF page index through
        the pre-existing time_of_frame_ms metadata key to maintain compatibility.
        """
        text_list = []
        image_list = []
        metadatas = []
        for item in annotation:
            page_index = item["frame_no"]
            image_index = item["sub_video_id"]
            path_to_image = os.path.join(path_to_files, f"page{page_index}_image{image_index}.png")
            caption_for_ingesting = item["caption"]
            caption_for_inference = item["caption"]

            pdf_id = item["video_id"]
            b64_img_str = item["b64_img_str"]
            embedding_type = "pair" if b64_img_str else "text"
            source = item["video_name"]

            text_list.append(caption_for_ingesting)

            if b64_img_str:
                image_list.append(path_to_image)

            metadatas.append(
                {
                    "content": caption_for_ingesting,
                    "b64_img_str": b64_img_str,
                    "video_id": pdf_id,
                    "source_video": source,
                    "time_of_frame_ms": page_index,  # For PDFs save the page number
                    "embedding_type": embedding_type,
                    "title": title,
                    "transcript_for_inference": caption_for_inference,
                }
            )

        return text_list, image_list, metadatas

    def ingest_multimodal(self, filename, data_folder, embeddings, is_pdf=False):
        """Ingest text image pairs to Milvus from the data/ directory that consists of frames and annotations."""
        data_folder = os.path.abspath(data_folder)
        annotation_file_path = os.path.join(data_folder, "annotations.json")
        path_to_frames = os.path.join(data_folder, "frames")

        annotation = load_json_file(annotation_file_path)

        # prepare data to ingest
        if is_pdf:
            text_list, image_list, metadatas = self.prepare_pdf_data_from_annotation(
                annotation, path_to_frames, filename
            )
        else:
            text_list, image_list, metadatas = self.prepare_data_and_metadata_from_annotation(
                annotation, path_to_frames, filename
            )

        MultimodalMilvus.from_text_image_pairs_return_keys(
            texts=[f"From {filename}. " + text for text in text_list],
            images=image_list,
            embedding=embeddings,
            metadatas=metadatas,
            collection_name=COLLECTION_NAME,
            milvus_uri=MILVUS_URI,
            filename=filename,
        )

    async def ingest_generate_transcripts(self, files: List[UploadFile] = File(None)):
        """Upload videos or audio files with speech, generate transcripts using whisper and ingest into milvus."""

        if files:
            files_to_ingest = []
            uploaded_files_map = {}
            for file in files:
                if os.path.splitext(file.filename)[1] in [".mp4", ".wav"]:
                    files_to_ingest.append(file)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {file.filename} is not an mp4 file. Please upload mp4 files only.",
                    )

            for file_to_ingest in files_to_ingest:
                st = time.time()
                file_extension = os.path.splitext(file_to_ingest.filename)[1]
                is_video = file_extension == ".mp4"
                file_type_str = "video" if is_video else "audio file"
                logger.info(f"Processing {file_type_str} {file_to_ingest.filename}")

                # Assign unique identifier to video
                file_id = generate_id()

                # Create video file name by appending identifier
                base_file_name = os.path.splitext(file_to_ingest.filename)[0]
                file_name_with_id = f"{base_file_name}_{file_id}{file_extension}"
                dir_name = os.path.splitext(file_name_with_id)[0]

                # Save file in upload_directory
                with open(os.path.join(self.upload_folder, file_name_with_id), "wb") as f:
                    shutil.copyfileobj(file_to_ingest.file, f)

                uploaded_files_map[base_file_name] = file_name_with_id

                if is_video:
                    # Extract temporary audio wav file from video mp4
                    audio_file = dir_name + ".wav"
                    logger.info(f"Extracting {audio_file}")
                    convert_video_to_audio(
                        os.path.join(self.upload_folder, file_name_with_id),
                        os.path.join(self.upload_folder, audio_file),
                    )
                    logger.info(f"Done extracting {audio_file}")
                else:
                    # We already have an audio file
                    audio_file = file_name_with_id

                # Load whisper model
                logger.info("Loading whisper model....")
                whisper_model = load_whisper_model(model_name=WHISPER_MODEL)
                logger.info("Done loading whisper!")

                # Extract transcript from audio
                logger.info("Extracting transcript from audio")
                transcripts = extract_transcript_from_audio(whisper_model, os.path.join(self.upload_folder, audio_file))

                # Save transcript as vtt file and delete audio file
                vtt_file = dir_name + ".vtt"
                write_vtt(transcripts, os.path.join(self.upload_folder, vtt_file))
                if is_video:
                    delete_audio_file(os.path.join(self.upload_folder, audio_file))
                logger.info("Done extracting transcript.")

                if is_video:
                    # Store frames and caption annotations in a new directory
                    logger.info("Extracting frames and generating annotation")
                    extract_frames_and_annotations_from_transcripts(
                        file_id,
                        os.path.join(self.upload_folder, file_name_with_id),
                        os.path.join(self.upload_folder, vtt_file),
                        os.path.join(self.upload_folder, dir_name),
                        compress_images=True,
                    )
                else:
                    # Generate annotations based on the transcript
                    logger.info("Generating annotations for the transcription")
                    generate_annotations_from_transcript(
                        file_id,
                        os.path.join(self.upload_folder, file_name_with_id),
                        os.path.join(self.upload_folder, vtt_file),
                        os.path.join(self.upload_folder, dir_name),
                    )

                logger.info("Done extracting frames and generating annotation")
                # Delete temporary vtt file
                os.remove(os.path.join(self.upload_folder, vtt_file))

                # Ingest multimodal data into milvus
                logger.info("Ingesting data to milvus vector store")
                self.ingest_multimodal(base_file_name, os.path.join(self.upload_folder, dir_name), self.embeddings)

                # Delete temporary video directory containing frames and annotations
                shutil.rmtree(os.path.join(self.upload_folder, dir_name))

                logger.info(f"Processed file {file_to_ingest.filename}")
                end = time.time()
                logger.info(str(end - st))

            return {
                "status": 200,
                "message": "Data preparation succeeded",
                "file_id_maps": uploaded_files_map,
            }

        raise HTTPException(status_code=400, detail="Must provide at least one video (.mp4) or audio (.wav) file.")

    async def ingest_generate_captions(self, files: List[UploadFile] = File(None)):
        """Upload images and videos without speech (only background music or no audio), generate captions using lvm microservice and ingest into milvus."""

        if files:
            file_paths = []
            uploaded_files_saved_files_map = {}
            for file in files:
                if os.path.splitext(file.filename)[1] in [".mp4", ".png", ".jpg", ".jpeg", ".gif"]:
                    file_paths.append(file)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {file.filename} is not a supported file type. Please upload mp4, png, jpg, jpeg, and gif files only.",
                    )

            for file in file_paths:
                logger.info(f"Processing file {file.filename}")

                # Assign unique identifier to file
                id = generate_id()

                # Create file name by appending identifier
                name, ext = os.path.splitext(file.filename)
                file_name = f"{name}_{id}{ext}"
                dir_name = os.path.splitext(file_name)[0]

                # Save file in upload_directory
                with open(os.path.join(self.upload_folder, file_name), "wb") as f:
                    shutil.copyfileobj(file.file, f)
                uploaded_files_saved_files_map[name] = file_name

                # Store frames and caption annotations in a new directory
                await extract_frames_and_generate_captions(
                    id,
                    os.path.join(self.upload_folder, file_name),
                    LVM_ENDPOINT,
                    os.path.join(self.upload_folder, dir_name),
                    compress_images=True,
                )

                # Ingest multimodal data into milvus
                self.ingest_multimodal(name, os.path.join(self.upload_folder, dir_name), self.embeddings)

                # Delete temporary directory containing frames and annotations
                # shutil.rmtree(os.path.join(upload_folder, dir_name))

                logger.info(f"Processed file {file.filename}")

            return {
                "status": 200,
                "message": "Data preparation succeeded",
                "file_id_maps": uploaded_files_saved_files_map,
            }

        raise HTTPException(status_code=400, detail="Must provide at least one file.")

    async def ingest_files(self, files: Optional[Union[UploadFile, List[UploadFile]]] = File(None)):

        if logflag:
            logger.info(f"[ milvus ingest ] files:{files}")

        if files:
            accepted_media_formats = [".mp4", ".png", ".jpg", ".jpeg", ".gif", ".pdf"]
            # Create a lookup dictionary containing all media files
            matched_files = {
                f.filename: [f] for f in files if os.path.splitext(f.filename)[1] in accepted_media_formats
            }
            uploaded_files_map = {}

            for file in files:
                file_base, file_extension = os.path.splitext(file.filename)
                if file_extension == ".vtt":
                    if "{}.mp4".format(file_base) in matched_files:
                        matched_files["{}.mp4".format(file_base)].append(file)
                    else:
                        logger.info(f"No video was found for caption file {file.filename}.")
                elif file_extension == ".txt":
                    if "{}.png".format(file_base) in matched_files:
                        matched_files["{}.png".format(file_base)].append(file)
                    elif "{}.jpg".format(file_base) in matched_files:
                        matched_files["{}.jpg".format(file_base)].append(file)
                    elif "{}.jpeg".format(file_base) in matched_files:
                        matched_files["{}.jpeg".format(file_base)].append(file)
                    elif "{}.gif".format(file_base) in matched_files:
                        matched_files["{}.gif".format(file_base)].append(file)
                    else:
                        logger.info(f"No image was found for caption file {file.filename}.")
                elif file_extension not in accepted_media_formats:
                    logger.info(f"Skipping file {file.filename} because of unsupported format.")
            for media_file_name, file_list in matched_files.items():
                if len(file_list) != 2 and os.path.splitext(media_file_name)[1] != ".pdf":
                    raise HTTPException(status_code=400, detail=f"No caption file found for {media_file_name}")

            if len(matched_files.keys()) == 0:
                return HTTPException(
                    status_code=400,
                    detail="The uploaded files have unsupported formats. Please upload at least one video file (.mp4) with captions (.vtt) or one image (.png, .jpg, .jpeg, or .gif) with caption (.txt) or one .pdf file",
                )

            for media_file in matched_files:
                logger.info(f"Processing file {media_file}")
                file_name, file_extension = os.path.splitext(media_file)

                # Assign unique identifier to file
                file_id = generate_id()

                # Create file name by appending identifier
                media_file_name = f"{file_name}_{file_id}{file_extension}"
                media_dir_name = os.path.splitext(media_file_name)[0]

                # Save file in upload_directory
                with open(os.path.join(self.upload_folder, media_file_name), "wb") as f:
                    shutil.copyfileobj(matched_files[media_file][0].file, f)
                uploaded_files_map[file_name] = media_file_name

                if file_extension == ".pdf":
                    # Set up location to store pdf images and text, reusing "frames" and "annotations" from video
                    output_dir = os.path.join(self.upload_folder, media_dir_name)
                    os.makedirs(output_dir, exist_ok=True)
                    os.makedirs(os.path.join(output_dir, "frames"), exist_ok=True)
                    doc = pymupdf.open(os.path.join(self.upload_folder, media_file_name))
                    annotations = []
                    for page_idx, page in enumerate(doc, start=1):
                        text = page.get_text()
                        images = page.get_images()
                        for image_idx, image in enumerate(images, start=1):
                            # Write image and caption file for each image found in pdf
                            img_fname = f"page{page_idx}_image{image_idx}"
                            img_fpath = os.path.join(output_dir, "frames", img_fname + ".png")
                            pix = pymupdf.Pixmap(doc, image[0])  # create pixmap

                            if pix.n - pix.alpha > 3:  # if CMYK, convert to RGB first
                                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)

                            pix.save(img_fpath)  # pixmap to png
                            pix = None

                            # Resize the image
                            resize_image(img_fpath)

                            # Convert image to base64 encoded string
                            with open(img_fpath, "rb") as image2str:
                                encoded_string = base64.b64encode(image2str.read())  # png to bytes

                            decoded_string = encoded_string.decode()  # bytes to string

                            # Check the length of the decoded string
                            decoded_length = len(decoded_string)
                            print(f"Length of the decoded base64 string: {decoded_length}")

                            # Create annotations file, reusing metadata keys from video
                            annotations.append(
                                {
                                    "video_id": file_id,
                                    "video_name": os.path.basename(os.path.join(self.upload_folder, media_file_name)),
                                    "b64_img_str": decoded_string,
                                    "caption": text,
                                    "time": 0.0,
                                    "frame_no": page_idx,
                                    "sub_video_id": image_idx,
                                }
                            )
                else:
                    # Save caption file in upload directory
                    caption_file_extension = os.path.splitext(matched_files[media_file][1].filename)[1]
                    caption_file = f"{media_dir_name}{caption_file_extension}"
                    with open(os.path.join(self.upload_folder, caption_file), "wb") as f:
                        shutil.copyfileobj(matched_files[media_file][1].file, f)

                    # Store frames and caption annotations in a new directory
                    extract_frames_and_annotations_from_transcripts(
                        file_id,
                        os.path.join(self.upload_folder, media_file_name),
                        os.path.join(self.upload_folder, caption_file),
                        os.path.join(self.upload_folder, media_dir_name),
                        compress_images=True,
                    )

                    # Delete temporary caption file
                    os.remove(os.path.join(self.upload_folder, caption_file))

                    # Ingest multimodal data into milvus
                    self.ingest_multimodal(file_name, os.path.join(self.upload_folder, media_dir_name), self.embeddings)

                # Delete temporary media directory containing frames and annotations
                shutil.rmtree(os.path.join(self.upload_folder, media_dir_name))

                logger.info(f"Processed file {media_file}")

            return {
                "status": 200,
                "message": "Data preparation succeeded",
                "file_id_maps": uploaded_files_map,
            }

        raise HTTPException(
            status_code=400,
            detail="Must provide at least one pair consisting of video (.mp4) and captions (.vtt) or image (.png, .jpg, .jpeg, .gif) with caption (.txt)",
        )

    async def get_files(self):
        """Returns list of names of uploaded videos saved on the server."""

        if not Path(upload_folder).exists():
            logger.info("No file uploaded, return empty list.")
            return []

        uploaded_videos = os.listdir(upload_folder)
        return uploaded_videos

    async def delete_files(self, file_path: str = Body(..., embed=True)):
        if logflag:
            logger.info(f"[ milvus delete ] delete files: {file_path}")

        my_milvus = Milvus(
            embedding_function=self.embeddings,
            collection_name=COLLECTION_NAME,
            connection_args={"uri": MILVUS_URI},
            index_params=INDEX_PARAMS,
            auto_id=True,
        )

        # delete all uploaded files
        delete_all_data(my_milvus)

        # delete files on local disk
        try:
            clear_upload_folder(upload_folder)
        except Exception as e:
            if logflag:
                logger.info(f"[ milvus delete ] {e}. Fail to delete {upload_folder}.")
            raise HTTPException(status_code=500, detail=f"Fail to delete {upload_folder}: {e}")
        if logflag:
            logger.info("[ milvus delete ] successfully delete all files.")

        create_upload_folder(upload_folder)
        if logflag:
            logger.info("[ milvus delete ] new upload folder created.")
        return {"status": True}

    async def ingest_videos(self, files: List[UploadFile] = File(None)):
        pass

    async def get_videos(self):
        pass

    async def get_one_file(self, filename: str):
        pass
