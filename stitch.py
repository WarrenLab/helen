import h5py
import argparse
import sys
from modules.python.Stitch import Stitch
from modules.python.TextColor import TextColor
"""
The stitch module generates a consensus sequence from all the predictions we generated from call_consensus.py.

The method is as follows:
    1) MarginPolish
     - MarginPolish takes a "Region" which is usually 1000 bases and generates pileup summaries for them.
     - The region often is more than 1000 bases but for mini-batch to work, we make sure all the images are 1000 bases
       so MarginPolish chunks these Regions into images providing each Regional image with a chunk id.
    2) HELEN call consensus
     - Call Consensus generates a prediction for each of the images, but while saving it makes sure all the images
       that belong to the same region, gets saved under the same chunk_prefix, making sure all the sequences can
     - be aggregated easily.
    3) HELEN stitch
     -  The stitch method that loads one contig at a time and grabs all the "regions" with predictions. The regional
        chunks are easily stitched without having to do an overlap as the positions inside a region is consistent.
     - For regions that are adjacent, HELEN uses a local smith-waterman alignment to find an anchor point to
       stitch sequences from two adjacent sequences together.
"""


def process_marginpolish_h5py(hdf_file_path, output_path, output_prefix, threads):
    """
    This method gathers all contigs and calls the stitch module for each contig.
    :param hdf_file_path: Path to the prediction file.
    :param output_path: Path to the output_consensus_sequence
    :param output_prefix: Output file's prefix
    :param threads: Number of threads to use
    :return:
    """

    # we gather all the contigs
    contigs = []
    with h5py.File(hdf_file_path, 'r') as hdf5_file:
        if 'predictions' in hdf5_file:
            contigs = list(hdf5_file['predictions'].keys())
        else:
            raise ValueError(TextColor.RED + "ERROR: INVALID HDF5 FILE, FILE DOES NOT CONTAIN predictions KEY.\n"
                             + TextColor.END)

    # open an output fasta file
    # we should really use a fasta handler for this, I don't like this.
    output_filename = output_path + output_prefix + '.fa'
    consensus_fasta_file = open(output_filename, 'w')
    sys.stderr.write(TextColor.GREEN + "INFO: OUTPUT FILE: " + output_filename + "\n" + TextColor.END)

    # for each contig
    for i, contig in enumerate(contigs):
        log_prefix = "{:04d}".format(i) + "/" + "{:04d}".format(len(contigs)) + ":"
        sys.stderr.write(TextColor.GREEN + "INFO: " + str(log_prefix) + " PROCESSING CONTIG: " + contig + "\n"
                         + TextColor.END)

        # get all the chunk keys
        with h5py.File(hdf_file_path, 'r') as hdf5_file:
            chunk_keys = sorted(hdf5_file['predictions'][contig].keys())

        # call stitch to generate a sequence for this contig
        stich_object = Stitch()
        consensus_sequence = stich_object.create_consensus_sequence(hdf_file_path, contig, chunk_keys, threads)
        sys.stderr.write(TextColor.BLUE + "INFO: " + str(log_prefix) + " FINISHED PROCESSING " + contig
                         + ", POLISHED SEQUENCE LENGTH: " + str(len(consensus_sequence)) + ".\n" + TextColor.END)

        # if theres a sequence then write it to the file
        if consensus_sequence is not None:
            consensus_fasta_file.write('>' + contig + "\n")
            consensus_fasta_file.write(consensus_sequence+"\n")


if __name__ == '__main__':
    '''
    Processes arguments and performs tasks.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sequence_hdf",
        type=str,
        required=True,
        help="Path to a HDF5 file that was generated using call consensus."
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Path to the output directory."
    )
    parser.add_argument(
        "--threads",
        type=int,
        required=True,
        default=5,
        help="Number of maximum threads for this region."
    )
    parser.add_argument(
        "--output_prefix",
        type=str,
        required=False,
        default="HELEN_consensus",
        help="Prefix for the output file."
    )

    FLAGS, unparsed = parser.parse_known_args()
    process_marginpolish_h5py(FLAGS.sequence_hdf, FLAGS.output_dir, FLAGS.output_prefix, FLAGS.threads)
