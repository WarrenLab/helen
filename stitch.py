import h5py
import argparse
import sys
from Bio import pairwise2
from Bio.pairwise2 import format_alignment
from os.path import isfile, join
from os import listdir
import concurrent.futures
import numpy as np
from collections import  defaultdict
import operator

BASE_ERROR_RATE = 0.2
label_decoder = {1: 'A', 2: 'C', 3: 'G', 4: 'T', 0: ''}


def get_file_paths_from_directory(directory_path):
    """
    Returns all paths of files given a directory path
    :param directory_path: Path to the directory
    :return: A list of paths of files
    """
    file_paths = [join(directory_path, file) for file in listdir(directory_path) if isfile(join(directory_path, file)) and file[-2:] == 'h5']
    return file_paths


def chunks(file_names, threads):
    """Yield successive n-sized chunks from l."""
    chunks = []
    for i in range(0, len(file_names), threads):
        chunks.append(file_names[i:i + threads])
    return chunks


def small_chunk_stitch(file_name, contig, small_chunk_keys):
    # for chunk_key in small_chunk_keys:

    name_sequence_tuples = list()

    for chunk_name in small_chunk_keys:
        with h5py.File(file_name, 'r') as hdf5_file:
            contig_start = hdf5_file['predictions'][contig][chunk_name]['contig_start'][()]
            contig_end = hdf5_file['predictions'][contig][chunk_name]['contig_end'][()]

        with h5py.File(file_name, 'r') as hdf5_file:
            smaller_chunks = set(hdf5_file['predictions'][contig][chunk_name].keys()) - {'contig_start', 'contig_end'}

        all_positions = set()
        base_prediction_dict = defaultdict()
        rle_prediction_dict = defaultdict()
        for chunk in smaller_chunks:

            with h5py.File(file_name, 'r') as hdf5_file:
                bases = hdf5_file['predictions'][contig][chunk_name][chunk]['bases'][()]
                rles = hdf5_file['predictions'][contig][chunk_name][chunk]['rles'][()]
                positions = hdf5_file['predictions'][contig][chunk_name][chunk]['position'][()]

            positions = np.array(positions, dtype=np.int64)
            base_predictions = np.array(bases, dtype=np.int)
            rle_predictions = np.array(rles, dtype=np.int)

            for position, base_pred, rle_pred in zip(positions, base_predictions, rle_predictions):
                indx = position[1]
                pos = position[0]
                if indx < 0 or pos < 0:
                    continue
                if (pos, indx) not in base_prediction_dict:
                    base_prediction_dict[(pos, indx)] = base_pred
                    rle_prediction_dict[(pos, indx)] = rle_pred
                    all_positions.add((pos, indx))

        pos_list = sorted(list(all_positions), key=lambda element: (element[0], element[1]))
        dict_fetch = operator.itemgetter(*pos_list)
        predicted_base_labels = list(dict_fetch(base_prediction_dict))
        predicted_rle_labels = list(dict_fetch(rle_prediction_dict))
        sequence = ''.join([label_decoder[base] * int(rle) for base, rle in zip(predicted_base_labels,
                                                                                predicted_rle_labels)])
        name_sequence_tuples.append((contig, contig_start, contig_end, sequence))

    return name_sequence_tuples


def get_confident_positions(alignment_a, alignment_b):
    match_counter = 0
    a_index = 0
    b_index = 0

    for base_a, base_b in zip(alignment_a, alignment_b):
        if base_a != '-':
            a_index += 1

        if base_b != '-':
            b_index += 1

        if base_a == base_b:
            match_counter += 1
        else:
            match_counter = 0

        if match_counter >= 3:
            return a_index, b_index

    return -1, -1


def create_consensus_sequence(hdf5_file_path, contig, sequence_chunk_keys, threads):
    chunk_name_to_sequence = defaultdict()
    sequence_chunks = list()
    # generate the dictionary in parallel
    with concurrent.futures.ProcessPoolExecutor(max_workers=threads) as executor:
        file_chunks = chunks(sequence_chunk_keys, int(len(sequence_chunk_keys) / threads) + 1)

        futures = [executor.submit(small_chunk_stitch, hdf5_file_path, contig, file_chunk) for file_chunk in file_chunks]
        for fut in concurrent.futures.as_completed(futures):
            if fut.exception() is None:
                name_sequence_tuples = fut.result()
                for contig, contig_start, contig_end, sequence in name_sequence_tuples:
                    chunk_name_to_sequence[(contig, contig_start, contig_end)] = sequence
                    sequence_chunks.append((contig, contig_start, contig_end))
            else:
                sys.stderr.write("ERROR: " + str(fut.exception()) + "\n")
            fut._result = None  # python issue 27144

    # but you cant do this part in parallel, this has to be linear
    sequence_chunks = sorted(sequence_chunks, key=lambda element: (element[1], element[2]))

    _, running_start, running_end = sequence_chunks[0]
    running_sequence = chunk_name_to_sequence[(contig, running_start, running_end)]
    # if len(running_sequence) < 500:
    #     sys.stderr.write("ERROR: CURRENT SEQUENCE LENGTH TOO SHORT: " + sequence_chunk_keys[0] + "\n")
    #     exit()

    for i in range(1, len(sequence_chunks)):
        _, this_start, this_end = sequence_chunks[i]
        this_sequence = chunk_name_to_sequence[(contig, this_start, this_end)]
        print(this_start, this_end)

        if this_start < running_end:
            # overlap
            overlap_bases = running_end - this_start
            overlap_bases = overlap_bases + int(overlap_bases * BASE_ERROR_RATE)

            if overlap_bases > len(running_sequence):
                print("OVERLAP BASES ERROR WITH RUNNING SEQUENCE: ", sequence_chunks[i], running_end, this_end, overlap_bases, len(running_sequence))
            if overlap_bases > len(this_sequence):
                print("OVERLAP BASES ERROR WITH CURRENT SEQUENCE: ", sequence_chunks[i], running_end, this_end, overlap_bases, len(this_sequence))

            sequence_suffix = running_sequence[-overlap_bases:]
            sequence_prefix = this_sequence[:overlap_bases]
            alignments = pairwise2.align.globalxx(sequence_suffix, sequence_prefix)
            pos_a, pos_b = get_confident_positions(alignments[0][0], alignments[0][1])

            if pos_a == -1 or pos_b == -1:
                sys.stderr.write("ERROR: INVALID OVERLAPS: " + str(alignments[0]) + str(sequence_chunks[i])  + "\n")
                return None

            left_sequence = running_sequence[:-(overlap_bases-pos_a)]
            right_sequence = this_sequence[pos_b:]

            running_sequence = left_sequence + right_sequence
            running_end = this_end
        else:
            print("NO OVERLAP: POSSIBLE ERROR", sequence_chunks[i], contig, this_start, running_end, sequence_chunks[i])
            exit()

    sys.stderr.write("SUCCESSFULLY CALLED CONSENSUS SEQUENCE" + "\n")

    return running_sequence


def process_marginpolish_h5py(hdf_file_path, output_path, threads):
    with h5py.File(hdf_file_path, 'r') as hdf5_file:
        contigs = list(hdf5_file['predictions'].keys())

    consensus_fasta_file = open(output_path+'consensus.fa', 'w')
    for contig in contigs:
        with h5py.File(hdf_file_path, 'r') as hdf5_file:
            chunk_keys = sorted(hdf5_file['predictions'][contig].keys())

        consensus_sequence = create_consensus_sequence(hdf_file_path, contig, chunk_keys, threads)
        if consensus_sequence is not None:
            consensus_fasta_file.write('>' + contig + "\n")
            consensus_fasta_file.write(consensus_sequence+"\n")

    hdf5_file.close()


if __name__ == '__main__':
    '''
    Processes arguments and performs tasks.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sequence_hdf",
        type=str,
        required=True,
        help="H5PY file generated by HELEN."
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="CONSENSUS output directory."
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=5,
        help="Number of maximum threads for this region."
    )

    FLAGS, unparsed = parser.parse_known_args()
    process_marginpolish_h5py(FLAGS.sequence_hdf, FLAGS.output_dir, FLAGS.threads)
    # read_marginpolish_h5py(FLAGS.marginpolish_h5py_dir, FLAGS.output_h5py_dir, FLAGS.train_mode, FLAGS.threads)
