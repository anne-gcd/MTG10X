#!/usr/bin/env python3
#*****************************************************************************
#  Name: MTG-Link
#  Description: gap-filling tool for draft genome assemblies, dedicated to 
#  linked read data generated by 10XGenomics Chromium technology.
#  Copyright (C) 2020 INRAE
#  Author: Anne Guichard
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#*****************************************************************************

from __future__ import print_function
import os
import sys
import argparse
import csv
import re
import subprocess
from pathos.multiprocessing import ProcessingPool as Pool
#from multiprocessing import Pool
import gfapy
from gfapy.sequence import rc
from Bio import SeqIO, Align
from helpers import Gap, Scaffold, extract_barcodes, get_reads, mtg_fill, stats_align, get_position_for_edges, get_output_for_gfa, update_gfa_with_solution


#----------------------------------------------------
# Arg parser
#----------------------------------------------------
parser = argparse.ArgumentParser(prog="mtglink.py", usage="%(prog)s -gfa <input.gfa> -c <chunk_size> -bam <mapped.bam> -fastq <reads.fastq> -index <barcoded.shelve> [options]", \
                                description=("Gapfilling with linked read data, using MindTheGap in 'breakpoint' mode"))

parserMain = parser.add_argument_group("[Main options]")
parserMtg = parser.add_argument_group("[MindTheGap option]")

parserMain.add_argument('-gfa', dest="input_gfa", action="store", help="Input GFA file (GFA 2.0) (format: xxx.gfa)", required=True)
parserMain.add_argument('-c', dest="chunk", action="store", type=int, help="Chunk size (bp)", required=True)
parserMain.add_argument('-bam', dest="bam", action="store", help="BAM file: linked reads mapped on current genome assembly (format: xxx.bam)", required=True)
parserMain.add_argument('-fastq', dest="reads", action="store", help="File of indexed reads (format: xxx.fastq | xxx.fq)", required=True)
parserMain.add_argument('-index', dest="index", action="store", help="Prefix of barcodes index file (format: xxx.shelve)", required=True)
parserMain.add_argument('-f', dest="freq", action="store", type=int, default=2, help="Minimal frequence of barcodes extracted in the chunk of size '-c' [default: 2]")
parserMain.add_argument('-out', dest="outDir", action="store", default="./mtglink_results", help="Output directory [default './mtglink_results']")
parserMain.add_argument('-refDir', dest="refDir", action="store", help="Directory containing the reference sequences if any")
parserMain.add_argument('-line', dest="line", action="store", type=int, help="Line of GFA file input from which to start analysis (if not provided, start analysis from first line of GFA file input) [optional]")
parserMain.add_argument('-rbxu', dest="rbxu", action="store", help="File containing the reads of the union (if already extracted) [optional]")

parserMtg.add_argument('-k', dest="kmer", action="store", default=[51, 41, 31, 21],  nargs='*', type=int, help="k-mer size(s) used for gap-filling [default: [51, 41, 31, 21]]")
parserMtg.add_argument("--force", action="store_true", help="To force search on all '-k' values provided")
parserMtg.add_argument('-a', dest="abundance_threshold", action="store", default=[3, 2], nargs='*', type=int, help="Minimal abundance threshold for solid k-mers [default: [3, 2]]")
parserMtg.add_argument('-ext', dest="extension", action="store", type=int, default=500, help="Extension size of the gap on both sides (bp); determine start/end of gapfilling [default: '500']")
parserMtg.add_argument('-max-nodes', dest="max_nodes", action="store", type=int, default=1000, help="Maximum number of nodes in contig graph [default: 1000]")
parserMtg.add_argument('-max-length', dest="max_length", action="store", type=int, default=10000, help="Maximum length of gapfilling (bp) [default: 10000]")
parserMtg.add_argument('-nb-cores', dest="nb_cores", action="store", type=int, default=1, help="Number of cores [default: 1]")
parserMtg.add_argument('-max-memory', dest="max_memory", action="store", type=int, default=0, help="Max memory for graph building (in MBytes) [default: 0]")
parserMtg.add_argument('-verbose', dest="verbosity", action="store", type=int, default=0, help="Verbosity level [default: 0]")

args = parser.parse_args()

if re.match('^.*.gfa$', args.input_gfa) is None:
    parser.error("Warning: The suffix of the GFA file should be: '.gfa'")

if re.match('^.*.bam$', args.bam) is None:
    parser.error("Warning: The suffix of the BAM file should be: '.bam'")

#----------------------------------------------------
# Input files and arguments
#----------------------------------------------------
#GFA 2.0 file
gfa_file = os.path.abspath(args.input_gfa)
if not os.path.exists(gfa_file):
    parser.error("Warning: The path of the GFA file doesn't exist")
gfa_name = gfa_file.split('/')[-1]
print("\nInput GFA file: " + gfa_file)

#BAM file: linked reads mapped on current genome assembly
bam_file = os.path.abspath(args.bam)
if not os.path.exists(bam_file): 
    parser.error("Warning: The path of the BAM file doesn't exist")
print("BAM file: " + bam_file)

#Reads file: file of indexed reads
reads_file = os.path.abspath(args.reads)
if not os.path.exists(reads_file):
    parser.error("Warning: The path of the file of indexed reads doesn't exist")
print("File of indexed reads: " + reads_file)

#Prefix of barcodes index file
index_file = os.path.abspath(args.index)
print("Barcodes index file (prefix): " + index_file)

#Directory containing the reference sequences if any
if args.refDir is not None:
    refDir = os.path.abspath(args.refDir)
    if not os.path.exists(refDir):
        parser.error("Warning: The path of the directory containing the reference sequences doesn't exist")

#variable 'ext' is the size of the extension of the gap, on both sides [by default 500]
ext = args.extension


#----------------------------------------------------
# Directories for saving results
#----------------------------------------------------
cwd = os.getcwd() 

#outDir
if not os.path.exists(args.outDir):
    os.mkdir(args.outDir)
try:
    os.chdir(args.outDir)
except:
    print("Something wrong with specified directory. Exception-", sys.exc_info())
    print("Restoring the path")
    os.chdir(cwd)
outDir = os.getcwd()
print("\nThe results are saved in " + outDir + "\n")

#unionDir
unionDir = outDir + "/union"
os.mkdir(unionDir)

#mtgDir
mtgDir = outDir + "/mtg_results"
os.mkdir(mtgDir)

#contigDir
contigDir = outDir + "/contigs"
os.mkdir(contigDir)

#statsDir
statsDir = outDir + "/alignments_stats"


#----------------------------------------------------
# gapfilling function - Pipeline
#----------------------------------------------------
'''
To perform the gap-filling on a specific gap:
    - it takes as input the current gap on which we want to perform the gap-filling
    - it outputs the list 'union_summary' containing the gap ID, the names of the left and right flanking sequences, the gap size, the chunk size, and the number of barcodes and reads extracted on the chunks to perform the gap-filling
    - it outputs as well the list 'output_for_gfa' containing the gap-filled sequence's name, as well as its length, its sequence, the number of solution found, the beginning and ending positions of the overlap and the quality of the sequence
'''
def gapfilling(current_gap):

    os.chdir(outDir)

    #Open the input GFA file to get the corresponding Gap line ('G' line)
    gfa = gfapy.Gfa.from_file(gfa_file)
    for _gap_ in gfa.gaps:
        if str(_gap_) == current_gap:
            current_gap = _gap_
            #Create the object 'gap' from the class 'Gap'
            gap = Gap(current_gap)

    #Get some information on the current gap we are working on
    gap.info()
    gap_label = gap.label()

    #Create two objects ('left_scaffold' and 'right_scaffold') from the class 'Scaffold'
    left_scaffold = Scaffold(current_gap, gap.left, gfa_file)
    right_scaffold = Scaffold(current_gap, gap.right, gfa_file)

    #If chunk size larger than length of scaffold(s), set the chunk size to the minimal scaffold length
    #chunk_L
    if args.chunk > left_scaffold.slen:
        print("Warning for {}: The chunk size you provided is higher than the length of the left scaffold. Thus, for the left scaffold, the barcodes will be extracted on its whole length".format(gap_label))
        chunk_L = left_scaffold.slen
    else:
        chunk_L = args.chunk
    #chunk_R
    if args.chunk > right_scaffold.slen:
        print("Warning for {}: The chunk size you provided is higher than the length of the right scaffold. Thus, for the right scaffold, the barcodes will be extracted on its whole length".format(gap_label))
        chunk_R = right_scaffold.slen
    else:
        chunk_R = args.chunk

    #----------------------------------------------------
    # BamExtractor
    #----------------------------------------------------
    #Union output directory
    os.chdir(unionDir)
    
    #Initiate a dictionary to count the occurences of each barcode
    barcodes_occ = {}
    
    #Obtain the left barcodes that are extracted on the left region and store the barcodes and their occurences in the dict 'barcodes_occ'
    left_region = left_scaffold.chunk(chunk_L)
    extract_barcodes(bam_file, gap_label, left_region, barcodes_occ)

    #Obtain the right barcodes that are extracted on the right region and store the barcodes and their occurences in the dict 'barcodes_occ'
    right_region = right_scaffold.chunk(chunk_R)
    extract_barcodes(bam_file, gap_label, right_region, barcodes_occ)

    #Do the union of the barcodes on both left and right regions
    union_barcodes_file = "{}.{}.g{}.c{}.bxu".format(gfa_name, str(gap_label), gap.length, args.chunk)
    with open(union_barcodes_file, "w") as union_barcodes:
        #Filter barcodes by freq
        for (barcode, occurences) in barcodes_occ.items():
            if occurences >= args.freq:
                union_barcodes.write(barcode + "\n")

    #----------------------------------------------------
    # GetReads
    #----------------------------------------------------
    #If the reads of the union are already extracted, use the corresponding file
    if args.rbxu is not None:
        union_reads_file = os.path.abspath(args.rbxu)

    #Union: extract the reads associated with the barcodes
    else:
        union_reads_file = "{}.{}.g{}.c{}.rbxu.fastq".format(gfa_name, str(gap_label), gap.length, args.chunk)
        with open(union_reads_file, "w") as union_reads:
            get_reads(reads_file, index_file, gap_label, union_barcodes_file, union_reads)

    #----------------------------------------------------
    # Summary of union (barcodes and reads)
    #----------------------------------------------------
    bxu = sum(1 for line in open(union_barcodes_file, "r"))
    rbxu = sum(1 for line in open(union_reads_file, "r"))/4
    union_summary = [str(gap.identity), str(gap.left), str(gap.right), gap.length, args.chunk, bxu, rbxu]

    #Remove the barcodes files
    subprocess.run(["rm", union_barcodes_file])

    #----------------------------------------------------
    # MindTheGap pipeline
    #----------------------------------------------------        
    #Get flanking contigs sequences
    seq_L = str(left_scaffold.sequence())
    seq_R = str(right_scaffold.sequence())

    #Execute MindTheGap fill module on the union, in breakpoint mode
    #Iterate over the kmer values, starting with the highest
    for k in args.kmer:

        #MindTheGap output directory
        os.chdir(mtgDir)
    
        #----------------------------------------------------
        # Breakpoint file, with offset of size k removed
        #----------------------------------------------------
        bkpt_file = "{}.{}.g{}.c{}.k{}.offset_rm.bkpt.fasta".format(gfa_name, str(gap_label), gap.length, args.chunk, k)
        with open(bkpt_file, "w") as bkpt:

            #Left kmer and Reverse Right kmer (dependent on orientation left scaffold)
            line1 = ">bkpt1_GapID.{}_Gaplen.{} left_kmer.{}_len.{} offset_rm\n".format(str(gap_label), gap.length, left_scaffold.name, k)
            line2 = seq_L[(left_scaffold.slen - ext - k):(left_scaffold.slen - ext)]
            line7 = "\n>bkpt2_GapID.{}_Gaplen.{} right_kmer.{}_len.{} offset_rm\n".format(str(gap_label), gap.length, left_scaffold.name, k)
            line8 = str(rc(seq_L)[ext:(ext + k)])

            #Right kmer and Reverse Left kmer (dependent on orientation right scaffold)
            line3 = "\n>bkpt1_GapID.{}_Gaplen.{} right_kmer.{}_len.{} offset_rm\n".format(str(gap_label), gap.length, right_scaffold.name, k)
            line4 = seq_R[ext:(ext + k)]
            line5 = "\n>bkpt2_GapID.{}_Gaplen.{} left_kmer.{}_len.{} offset_rm\n".format(str(gap_label), gap.length, right_scaffold.name, k)
            line6 = str(rc(seq_R)[(right_scaffold.slen - ext - k):(right_scaffold.slen - ext)])

            bkpt.writelines([line1, line2, line3, line4, line5, line6, line7, line8])

        #----------------------------------------------------
        # Gapfilling
        #----------------------------------------------------
        #Iterate over the abundance threshold values, starting with the highest
        for a in args.abundance_threshold:

            print("\nGapfilling of {} for k={} and a={} (union)".format(str(gap_label), k, a))
            
            #Input arguments for MindTheGap
            input_file = os.path.join(unionDir, union_reads_file)
            output = "{}.{}.g{}.c{}.k{}.a{}.bxu".format(gfa_name, str(gap_label), gap.length, args.chunk, k, a)
            max_nodes = args.max_nodes
            max_length = args.max_length
            if max_length == 10000 and gap.length >= 10000:
                max_length = gap.length + 1000
            nb_cores = args.nb_cores
            max_memory = args.max_memory
            verbose = args.verbosity

            #Perform the gap-filling with MindTheGap
            mtg_fill(gap_label, input_file, bkpt_file, k, a, max_nodes, max_length, nb_cores, max_memory, verbose, output)

            #If at least one solution is found, perform qualitative evaluation of the gap-filled sequence(s)
            if os.path.getsize(mtgDir +"/"+ output + ".insertions.fasta") > 0:
                insertion_file = os.path.abspath(mtgDir +"/"+ output + ".insertions.fasta")

                #Modify the 'insertion_file' and save it to a new file ('input_file') so that the 'solution x/y' part appears in record.id (and not just in record.description)
                input_file = os.path.abspath(mtgDir +"/"+ output + "..insertions.fasta")
                with open(insertion_file, "r") as original, open(input_file, "w") as corrected:
                    records = SeqIO.parse(original, "fasta")
                    for record in records:
                        if "solution" in record.description:
                            record.id = record.id + "_sol_" + record.description.split(" ")[-1]
                        else:
                            record.id = record.id + "_sol_1/1"
                        SeqIO.write(record, corrected, "fasta")

                #----------------------------------------------------
                # Stats of the alignments query_seq vs reference_seq
                #----------------------------------------------------
                #Qualitative evaluation with the reference sequence
                if args.refDir is not None:
                    for file_ in os.listdir(refDir):
                        if str(gap_label) in file_:
                            ref_file = refDir +"/"+ str(file_)
                    if not os.path.isfile(ref_file):
                        print("Warning: No reference file was found for this gap. The qualitative evaluation will be performed with the flanking contigs information.")
            
                #Qualitative evalution with the flanking contigs information
                elif (args.refDir is None) or (ref_file is None):

                    #Merge both left and right flanking contigs sequences into a unique file (ref_file)
                    ref_file = contigDir +"/"+ str(gap_label) +".g"+ str(gap.length) + ".contigs.fasta"
                    with open(ref_file, "w") as ref_fasta:

                        #Left scaffold oriented '+'
                        if left_scaffold.orient == "+":
                            ref_fasta.write(">" + left_scaffold.name + "_region:" + str(left_scaffold.slen-ext) + "-" + str(left_scaffold.slen) + "\n")
                            ref_fasta.write(seq_L[(left_scaffold.slen - ext):left_scaffold.slen])
                        #Left scaffold oriented '-' ~ Right scaffold oriented '+'
                        elif left_scaffold.orient == "-":
                            ref_fasta.write(">" + left_scaffold.name + "_region:0-" + str(ext) + "\n")
                            ref_fasta.write(str(rc(seq_L)[0:ext]))

                        #Right scaffold oriented '+'
                        if right_scaffold.orient == "+":
                            ref_fasta.write("\n>" + right_scaffold.name + "_region:0-" + str(ext) + "\n")
                            ref_fasta.write(seq_R[0:ext])
                        #Right scaffold oriented '-' ~ Left scaffold oriented '+'
                        elif right_scaffold.orient == "-":
                            ref_fasta.write("\n>" + right_scaffold.name + "_region:" + str(right_scaffold.slen-ext) + "-" + str(right_scaffold.slen) + "\n")
                            ref_fasta.write(str(rc(seq_R)[(right_scaffold.slen - ext):right_scaffold.slen]))

                if not os.path.isfile(ref_file):
                    print("Warning: Something wrong with the specified reference file. Exception-", sys.exc_info())

                #Do statistics on the alignments of query_seq (found gapfill seq) vs reference
                else:
                    prefix = "{}.k{}.a{}".format(str(gap_label), k, a) 
                    stats_align(gap_label, input_file, ref_file, str(ext), prefix, statsDir)

                #----------------------------------------------------
                # Estimate quality of gapfilled sequence
                #----------------------------------------------------
                #Reader for alignment stats' files
                ref_qry_file = statsDir + "/" + prefix + ".ref_qry.alignment.stats"
                qry_qry_file = statsDir + "/" + prefix + ".qry_qry.alignment.stats"

                if not os.path.exists(ref_qry_file):
                    print("Warning: The '{}' file doesn't exits".format(ref_qry_file))
                    stats = False
                elif not os.path.exists(qry_qry_file):
                    print("Warning: The '{}' file doesn't exits".format(qry_qry_file))
                    stats = False

                else:
                    stats = True
                    ref_qry_output = open(ref_qry_file)
                    qry_qry_output = open(qry_qry_file)

                    reader_ref_stats = csv.DictReader(ref_qry_output, \
                                                    fieldnames=("Gap", "Len_gap", "Chunk", "k", "a", "Strand", "Solution", "Len_Q", "Ref", "Len_R", \
                                                                "Start_ref", "End_ref", "Start_qry", "End_qry", "Len_alignR", "Len_alignQ", "%_Id", "%_CovR", "%_CovQ", "Frame_R", "Frame_Q", "Quality"), \
                                                    delimiter='\t')

                    reader_revcomp_stats = csv.DictReader(qry_qry_output, \
                                                        fieldnames=("Gap", "Len_gap", "Chunk", "k", "a", "Solution1", "Len_Q1", "Solution2", "Len_Q2", \
                                                                    "Start_Q1", "End_Q1", "Start_Q2", "End_Q2", "Len_align_Q1", "Len_align_Q2", "%_Id", "%_Cov_Q1", "%_Cov_Q2", "Frame_Q1", "Frame_Q2", "Quality"), \
                                                        delimiter='\t')
                    
                    #Obtain a quality score for each gapfilled seq
                    solutions = []
                    output_for_gfa = []
                    insertion_quality_file = os.path.abspath(mtgDir +"/"+ output + ".insertions_quality.fasta")
                    with open(input_file, "r") as query, open(insertion_quality_file, "w") as qualified:
                        for record in SeqIO.parse(query, "fasta"):

                            seq = record.seq
                            strand = str(record.id).split('_')[0][-1]

                            #----------------------------------------------------
                            #Ref = reference sequence of simulated gap
                            #----------------------------------------------------
                            if args.refDir is not None:
                                #quality score for stats about the ref
                                quality_ref = []
                                for row in reader_ref_stats:
                                    if (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")):
                                        quality_ref.append(row["Quality"])
                                
                                if quality_ref == []:
                                    quality_ref.append('D')

                                ref_qry_output.seek(0)

                                #quality score for stats about the reverse complement strand
                                quality_revcomp = []
                                for row in reader_revcomp_stats:
                                    if ((record.id.split('_')[-1] in row["Solution1"]) and (("bkpt1" in record.id and "fwd" in row["Solution1"]) or ("bkpt2" in record.id and "rev" in row["Solution1"]))) \
                                        or ((record.id.split('_')[-1] in row["Solution2"]) and (("bkpt1" in record.id and "fwd" in row["Solution2"]) or ("bkpt2" in record.id and "rev" in row["Solution2"]))):
                                        quality_revcomp.append(row["Quality"])
                                if quality_revcomp == []:
                                    quality_revcomp.append('D')
                                qry_qry_output.seek(0)

                                #global quality score
                                quality_gapfilled_seq = min(quality_ref) + min(quality_revcomp)
                                
                                record.description = "Quality " + str(quality_gapfilled_seq)
                                SeqIO.write(record, qualified, "fasta")

                                #Update GFA with only the good solutions (the ones having a good quality score)
                                if (len(seq) > 2*ext) and (re.match('^.*Quality [AB]{2}$', record.description)):
                                    check = "True_" + str(strand)
                                    solutions.append(check)
                                    gfa_output = get_output_for_gfa(record, ext, k, gap.left, gap.right, left_scaffold, right_scaffold)
                                    output_for_gfa.append(gfa_output)
                                else:
                                    check = "False_" + str(strand)
                                    solutions.append(check)
    
                            #----------------------------------------------------
                            #Ref = flanking contigs' sequences
                            #----------------------------------------------------
                            else:
                                #quality score for stats about the extension
                                quality_ext_left = []
                                quality_ext_right = []
                                for row in reader_ref_stats:
                                    if (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")) and (row["Ref"] == left_scaffold.name):
                                        quality_ext_left.append(row["Quality"])
                                    elif (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")) and (row["Ref"] == right_scaffold.name):
                                        quality_ext_right.append(row["Quality"])
                                if quality_ext_left == []:
                                    quality_ext_left.append('D')
                                if quality_ext_right == []:
                                    quality_ext_right.append('D')

                                ref_qry_output.seek(0)

                                #quality score for stats about the reverse complement strand
                                quality_revcomp = []
                                for row in reader_revcomp_stats:
                                    if ((record.id.split('_')[-1] in row["Solution1"]) and (("bkpt1" in record.id and "fwd" in row["Solution1"]) or ("bkpt2" in record.id and "rev" in row["Solution1"]))) \
                                        or ((record.id.split('_')[-1] in row["Solution2"]) and (("bkpt1" in record.id and "fwd" in row["Solution2"]) or ("bkpt2" in record.id and "rev" in row["Solution2"]))):
                                        quality_revcomp.append(row["Quality"])
                                if quality_revcomp == []:
                                    quality_revcomp.append('D')
                                qry_qry_output.seek(0)

                                #global quality score
                                quality_gapfilled_seq = min(quality_ext_left) + min(quality_ext_right) + min(quality_revcomp)

                                record.description = "Quality " + str(quality_gapfilled_seq)
                                SeqIO.write(record, qualified, "fasta")

                                #Update GFA with only the good solutions (the ones having a good quality score)
                                if (len(seq) > 2*ext) and (re.match('^.*Quality A[AB]{2}$', record.description) or re.match('^.*Quality BA[AB]$', record.description)):
                                    check = "True_" + str(strand)
                                    solutions.append(check)
                                    gfa_output = get_output_for_gfa(record, ext, k, gap.left, gap.right, left_scaffold, right_scaffold)
                                    output_for_gfa.append(gfa_output)

                                else:
                                    check = "False_" + str(strand)
                                    solutions.append(check)

                        qualified.seek(0)

                    #remove the 'input_file' once done with it
                    subprocess.run(["rm", input_file])

                    #remplace the 'insertion_file' by the 'insertion_quality_file' (which is then renamed 'insertion_file')
                    subprocess.run(["rm", insertion_file])
                    subprocess.run(['mv', insertion_quality_file, insertion_file])


                #If at least one good solution for both fwd and rev strands amongst all solution found, stop searching
                if (stats == True) and ("True_1" and "True_2" in solutions): 
                        solution = True
                        break

                else:
                    solution = False
                    os.chdir(mtgDir)
            

            #If no solution found, remove the 'xxx.insertions.fasta' and 'xxx.insertions.vcf' file, and set 'solution' to False
            else:
                output_for_gfa = []
                insertion_fasta = os.path.abspath(mtgDir +"/"+ output + ".insertions.fasta")
                insertion_vcf = os.path.abspath(mtgDir +"/"+ output + ".insertions.vcf")
                subprocess.run(["rm", insertion_fasta])
                subprocess.run(["rm", insertion_vcf])
                solution = False


        if solution == True and not args.force:
            break

        #----------------------------------------------------
        # GFA output: case gap, no solution
        #----------------------------------------------------
        elif k == min(args.kmer) and a == min(args.abundance_threshold):
            #Save the current G line into the variable 'output_for_gfa' only if this variable is empty 
            #(e.g. in the case where solution == False because we found only a good solution for one strand (and not for both strands), we update the output GFA file with this good solution, not with a gap line)
            if len(output_for_gfa) == 0:
                output_for_gfa.append([str(current_gap)])


    

    #TODO: remove the flanking_contig.fasta files

    os.chdir(outDir)


    return union_summary, output_for_gfa


#----------------------------------------------------
# Gapfilling with MindTheGap
#----------------------------------------------------
try:
    #Open the input GFA file
    gfa = gfapy.Gfa.from_file(gfa_file)
    #Create the output GFA file
    out_gfa_file = str(gfa_name).split('.gfa')[0] + "_mtglink.gfa"

    #----------------------------------------------------
    # GFA output: case no gap
    #----------------------------------------------------
    #If no gap, rewrite all the lines into GFA output
    if len(gfa.gaps) == 0:
        with open(out_gfa_file, "w") as f:
            out_gfa = gfapy.Gfa()
            for line in gfa.lines:
                out_gfa.add_line(str(line))
            out_gfa.to_file(out_gfa_file)

    #----------------------------------------------------   
    # Fill the gaps
    #----------------------------------------------------
    #If gap, rewrite the H and S lines into GFA output
    if args.line is None:
        with open(out_gfa_file, "w") as f:
            out_gfa = gfapy.Gfa()
            out_gfa.add_line("H\tVN:Z:2.0")
            for line in gfa.segments:
                out_gfa.add_line(str(line))
            out_gfa.to_file(out_gfa_file)
        
    gaps = []
    gaps_label = []
    #If '-line' argument provided, start analysis from this line in GFA file input
    if args.line is not None:
        for _gap_ in gfa.gaps[(args.line - (len(gfa.segments)+2)):]:
            _gap_ = str(_gap_)
            gaps.append(_gap_)
    else:
        #Convert Gfapy gap line to a string to be able to use it with multiprocessing
        for _gap_ in gfa.gaps:
            _gap_ = str(_gap_)
            gaps.append(_gap_)

    p = Pool()

    with open("{}.union.sum".format(gfa_name), "w") as union_sum:
        legend = ["Gap_ID", "Left_scaffold", "Right_scaffold", "Gap_size", "Chunk_size", "Nb_barcodes", "Nb_reads"]
        union_sum.write('\t'.join(j for j in legend))

        for union_summary, output_for_gfa in p.map(gapfilling, gaps):
            #Write all union_summary (obtained for each gap) from 'gapfilling' into the 'union_sum' file
            union_sum.write("\n" + '\t'.join(str(i) for i in union_summary))

            #Output the 'output_for_gfa' results (obtained for each gap) from 'gapfilling' in the output GFA file
            print("\nCreating the output GFA file...")
            if len(output_for_gfa[0]) > 1:          #solution found for the current gap
                for output in output_for_gfa:
                    gapfill_file = update_gfa_with_solution(outDir, gfa_name, output, out_gfa_file)
                    success = True
            else:                                   #no solution found for the current gap
                out_gfa = gfapy.Gfa.from_file(out_gfa_file)
                out_gfa.add_line(output_for_gfa[0][0])
                out_gfa.to_file(out_gfa_file)
                success = False


        p.close()

    #Remove the raw files obtained from MindTheGap
    os.chdir(mtgDir)
    subprocess.run("rm -f *.h5", shell=True)
    subprocess.run("rm -f *.vcf", shell=True)


except Exception as e:
    print("\nException-")
    print(e)
    exc_type, exc_obj, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    print(exc_type, fname, exc_tb.tb_lineno)
    sys.exit(1)


print("\nThe results from MTG-Link are saved in " + outDir)
print("The results from MindTheGap are saved in " + mtgDir)
print("The statistics from MTG-Link are saved in " + statsDir)
print("Summary of the union: " +gfa_name+".union.sum")
print("GFA output file: " + out_gfa_file)
if success == True:
    print("Corresponding file containing all gapfill sequences: " + gapfill_file + "\n")

#----------------------------------------------------
#Summary output
#----------------------------------------------------
gfa_output = gfapy.Gfa.from_file(outDir +"/"+ str(out_gfa_file))

#Total initials gaps
total_gaps = []
for g_line in gfa.gaps:
    gap_start = str(g_line.sid1) +"_"+ str(g_line.sid2) 
    total_gaps.append(gap_start)
nb_total_gaps = len(total_gaps)
print("------------------------------------------------------------------------------------------------------------------------\n")
print("Attempt to gap-fill {} gaps \n".format(nb_total_gaps))

#Gap(s) not gap-filled
no_gapfill = []
for g_line in gfa_output.gaps:
    gap_end = str(g_line.sid1) +"_"+ str(g_line.sid2) 
    no_gapfill.append(gap_end)
    print("The gap {} was not successfully gap-filled".format(gap_end))

nb_gapfill = len(total_gaps) - len(no_gapfill)
print("\nIn total, {} gaps were successfully gap-filled:\n".format(str(nb_gapfill)))


#Gaps gap-filled
out_fasta_file = outDir +"/"+ gapfill_file
gap_names = []
if (out_fasta_file) is not None:
    with open(out_fasta_file, "r") as gapfilled:
        for record in SeqIO.parse(gapfilled, "fasta"):
            gap_name = str(record.id).split('_')[0]

            #For a new gap
            if gap_name not in gap_names:
                gap_names.append(gap_name)
                k = str(record.id).split('.k')[-1].split('_')[0]
                print("\t* " + gap_name + "\tk" + k)

            #For all gaps
            orientation = str(record.id).split('_')[-1]
            length = str(record.description).split('_ len_')[1].split('_qual_')[0]
            quality = str(record.description).split('_qual_')[1]
            print("\t\t* " + orientation + "\t" + length + " bp\t" + quality)
           
print("\n")

#TODO: two modules, one when reference sequence provided (args.refDir), one when no reference sequence is provided (args.scaff)