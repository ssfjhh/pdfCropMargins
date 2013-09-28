#!/usr/bin/python
# -*- coding: utf-8 -*-
# Note that using the shebang "usr/bin/env python" does not set the process
# name to pdfCropMargins in Linux (for things like top, ps, and killall).
"""

pdfCropMargins -- crop the margins of a PDF file

A command-line application to crop the margins of PDF files.  Cropping the
margins can make it easier to read the pages of a PDF document -- whether it
the document printed or displayed on a screen -- because the font appears
larger.  Margin-cropping is also useful at times when one PDF is included in
another as a graphic.  Many options are available.

To see the formatted documentation, run
   pdfCropMargins -h | more
or
   python pdfCropMargins -h | more

Copyright (c) 2013 Allen Barker, released under the MIT licence.
Project web site: <TODO, list project github>
Source code site: <TODO>
See http://opensource.org/licenses/MIT or the file LICENSE in the
source directory for the text of the license.

"""
 
# Some general notes, useful for reading the code.
#
# Margins are conveniently described by left, bottom, right, and top, but boxes
# in PDF files are usually defined by the lower-left point's x and y values
# followed by the upper-right point's x and y values.  This is equivalent
# information (since x and y is implicit in the margin names) but the
# viewpoint is slightly different.
#
# This program (like the Ghostscript program) uses the PDF ordering convention
# (lbrt) for listing margins and defining boxes.  Note that PIL uses some
# different conventions.  The origin in PDFs is the lower left going up but the
# origin in PIL images is the upper left going down.  Keep in mind that the
# program needs to make the proper conversion when rendering explicitly to
# images.  Also, the bounding box routine of PIL returns ltrb instead of lbrt.
# 
# TODO Options:
#    4) Name of a command to turn a page of PDF into an image file, taking two
#    file arguments and a resolution.  That should greatly help portability,
#    especially of the more advanced stuff that gs does not do.  Maybe a version
#    which does all the pages into separate files, since some progs do that?
# 
#    6) See pdfxchange docs for how to make it batch-process a PDF to images!?
#
#    7) Have option for using an arbitrary box for saving restore data, not
#    just ArtBox.  Note someone said that Adobe Illustrator actually sets
#    the ArtBox to the size of the picture... maybe TrimBox better default??
#    Downside of option is that we have to know which box was used, i.e., save it
#    in the Producer string...

#    Note that pdfxchange does not modify the Producer when it adds highlighting.

from __future__ import print_function, division
import sys, os, shutil, subprocess, tempfile

#
# Try to import the system pyPdf.  If that fails or if the '--pyPdfLocal'
# option was set then revert to the appropriate local version.
#

# Peek at the command line before fully parsing it later to see if we should import
# the local pyPdf.  (Works for simple option which are either set or not.)
pyPdfLocal = False
if __name__ == "__main__" and ("--pyPdfLocal" in sys.argv or "-pl" in sys.argv):
   pyPdfLocal = True

try:
   if pyPdfLocal: raise ImportError # just to run the except code
   from pyPdf import PdfFileWriter, PdfFileReader # the system's pyPdf
   from pyPdf.generic import \
         NameObject, createStringObject, RectangleObject, FloatObject
except ImportError:
   if not pyPdfLocal:
      print("\nWarning from pdfCropMargins: No system pyPdf Python package was"
            "\nfound.  Reverting to the local version packaged with this program."
            "\nTo silence this warning, use the '--pyPdfLocal' (or '-pl') option"
            "\non the command line.\n", file=sys.stderr)
   if pythonVersion[0] == "2":
      from mstamy2_PyPDF2_7da5545.PyPDF2 import PdfFileWriter, PdfFileReader 
      from mstamy2_PyPDF2_7da5545.PyPDF2.generic import \
            NameObject, createStringObject, RectangleObject, FloatObject
   else: # Python 3
      from mstamy2_PyPDF2_7da5545_py3.PyPDF2 import PdfFileWriter, PdfFileReader 
      from mstamy2_PyPDF2_7da5545_py3.PyPDF2.generic import \
            NameObject, createStringObject, RectangleObject, FloatObject

#
# Import the general functions for calculating a list of bounding boxes.
#

import calculate_bounding_boxes
from calculate_bounding_boxes import getBoundingBoxList

#
# Import the module that calls external programs.
#

import external_program_calls as ex

#
# Import the prettified argparse module and the actual documentation text.
#

from prettified_argparse import parseCommandLineArguments
from manpage_data import cmdParser

#
# Some general strings used by the program.
#

# The string which is appended to Producer metadata in cropped PDFs.
producerModifier = " (Cropped by pdfCropMargins.)"


#
# Begin general function definitions.
#


def generateDefaultFilename(infileName, croppedFile=True):
   """Generate the name of the default output file from the name of the input 
   file.  The croppedFile boolean is used to determine which 
   filename-modification string is used."""
   
   if croppedFile: suffix = prefix = args.stringCropped
   else: suffix = prefix = args.stringUncropped

   basename, extension = os.path.splitext(infileName)

   sep = args.stringSeparator
   if args.usePrefix: name = prefix + sep + basename + extension
   else: name = basename + sep + suffix + extension
   
   return name


def intersectBoxes(box1, box2):
   """Takes two pyPdf boxes (such as page.mediaBox) and returns the pyPdf
   box which is their intersection."""
   if not box1 and not box2: return None
   if not box1: return box2
   if not box2: return box1
   intersect = RectangleObject([0,0,0,0]) # Note [llx,lly,urx,ury] == [l,b,r,t]
   intersect.upperRight = (min(box1.upperRight[0], box2.upperRight[0]),
                           min(box1.upperRight[1], box2.upperRight[1]))
   intersect.lowerLeft =  (max(box1.lowerLeft[0], box2.lowerLeft[0]),
                           max(box1.lowerLeft[1], box2.lowerLeft[1]))
   return intersect


def getFullPageBox(page):
   """This returns whatever PDF box was selected (by the user option
   '--fullPageBox') to represent the full page size.  All cropping is done
   relative to this box.  The default selection option is the MediaBox
   intersected with the CropBox so multiple crops work as expected.  The
   argument page should be a pyPdf page object.  This function also by default
   sets the MediaBox and CropBox to the full-page size, and so should only be
   called once for each page."""

   # Save copies of old values in the page's namespace.
   page.originalMediaBox = page.mediaBox
   page.originalCropBox = page.cropBox

   firstLoop = True
   for boxString in args.fullPageBox:
      if boxString == "m": fBox = page.mediaBox
      if boxString == "c": fBox = page.cropBox
      if boxString == "t": fBox = page.trimBox
      if boxString == "a": fBox = page.artBox
      if boxString == "b": fBox = page.bleedBox

      # Take intersection over all chosen boxes.
      if not firstLoop:
         fullBox = intersectBoxes(fullBox, fBox)
      else:
         fullBox = fBox
      firstLoop = False

   page.mediaBox = fullBox
   page.cropBox = fullBox

   return fullBox


def getFullPageBoxList(inputDoc):
   """Get a list of all the full-page box values for each page.  The argument
   inputDoc should be a PdfFileReader object."""
   fullPageBoxList = []
   if args.verbose:
      print("Original full page sizes, in PDF format (lbrt):")
   for page in range(inputDoc.getNumPages()):
      # Get the current page and find the full-page box.
      currPage = inputDoc.getPage(page)

      fullPageBox = getFullPageBox(currPage)
      if args.verbose:
         print("   "+str(page+1)+"\t", fullPageBox) # page num numbering from 1

      # Convert the RectangleObject to floats in an ordinary list and append.
      fullPageBoxList.append([ float(b) for b in fullPageBox ])
   
   return fullPageBoxList


def calculateCropList(fullPageBoxList, boundingBoxList, pageNumsToCrop):
   """Given a list of full-page boxes (media boxes) and a list of tight
   bounding boxes for each page, calculate and return another list giving the
   list of bounding boxes to crop down to."""

   # Definition: the deltas are the four differences, one for each margin,
   # between the original full page box and the final, cropped full-page box.
   # In the usual case where margin sizes decrease these are the same as the
   # four margin-reduction values (in absolute points).   The deltas are
   # usually positive but they can be negative due to either percentRetain>100
   # or a large enough absolute offset (in which case the size of the
   # corresponding margin will increase).  When percentRetain<0 the deltas are
   # always greater than the absolute difference between the full page and a
   # tight bounding box, and so part of the text within the tight bounding box
   # will also be cropped (unless absolute offsets are used to counter that).

   numPages = len(boundingBoxList)
   pageRange = range(0, numPages)
   numPagesToCrop = len(pageNumsToCrop)

   # Handle the '--samePageSize' option.
   # Note that this is always done first, over the whole document, even before
   # pages and evenodd are handled.
   if args.samePageSize:
      if args.verbose: 
         print("\nSetting all page sizes to the largest bounding box that holds them all.")
      fullPageBoxList = [ [min(box[0] for box in fullPageBoxList),
                           min(box[1] for box in fullPageBoxList),
                           max(box[2] for box in fullPageBoxList),
                           max(box[3] for box in fullPageBoxList)]
                    ] * numPages
   
   # Handle the '--evenodd' option if it was selected.
   if args.evenodd:
      evenPageNumsToCrop = { pNum for pNum in pageNumsToCrop if pNum % 2 == 0 }
      oddPageNumsToCrop = { pNum for pNum in pageNumsToCrop if pNum % 2 != 0 }

      if args.uniform: uniformSetWithEvenOdd = True
      else: uniformSetWithEvenOdd = False

      # Recurse on even and odd pages, after resetting some options.
      if args.verbose: print("\nRecursively calculating crops for even and odd pages.")
      args.evenodd = False # avoid infinite recursion
      args.uniform = True  # --evenodd implies uniform, just on each separate group
      evenCropList = calculateCropList(fullPageBoxList, boundingBoxList, evenPageNumsToCrop)
      oddCropList = calculateCropList(fullPageBoxList, boundingBoxList, oddPageNumsToCrop)

      # Recombine the even and odd pages
      combineEvenOdd = []
      for pNum in pageRange:
         if pNum % 2 == 0: combineEvenOdd.append(evenCropList[pNum])
         else: combineEvenOdd.append(oddCropList[pNum])

      # Handle the case where --uniform was set with --evenodd
      if uniformSetWithEvenOdd:
         minBottomMargin = min( [ box[1] for box in combineEvenOdd ] )
         maxTopMargin = max( [ box[3] for box in combineEvenOdd ] )
         combineEvenOdd = [ [box[0], minBottomMargin, box[2], maxTopMargin]
                                                       for box in combineEvenOdd ]
      return combineEvenOdd

   # Calculate the list of deltas to be used to modify the original page sizes.
   # Basically, a delta is the absolute diff between the full and
   # tight-bounding boxes, scaled according to the user's percentRetain, with
   # any absolute offset then added (lb) or subtracted (tr) as appropriate.
   #
   # The deltas are all positive unless absoluteOffset changes that or
   # percent>100.  They are added (lb) or subtracted (tr) as appropriate.
   deltaList = []
   for tBox, fBox in zip(boundingBoxList, fullPageBoxList):
      deltas = [ abs(tBox[i] - fBox[i]) for i in range(4) ]
      adjDeltas = [ deltas[i] * (100.0-args.percentRetain[i]) / 100.0 for i in range(4) ]
      adjDeltas = [ adjDeltas[i] + args.absoluteOffset[i] for i in range(4) ]
      deltaList.append(adjDeltas)

   # Handle the '--uniform' options if one was selected.
   if args.uniformOrderPercent:
      percentVal = args.uniformOrderPercent[0]
      if percentVal < 0.0: percentVal = 0.0
      if percentVal > 100.0: percentVal = 100.0
      args.uniformOrderStat = [ int(round(numPagesToCrop * percentVal / 100.0)) ]

   if args.uniform or args.uniformOrderStat:
      if args.verbose: print("\nAll the selected pages will be uniformly cropped.")
      # Only look at the deltas which correspond to pages selected for cropping.
      # They will then be sorted for each margin and selected.
      cropDeltaList = [ deltaList[j] for j in pageRange if j in pageNumsToCrop ]

      i = 0 # Let i be the index value into the sorted delta list.
      if args.uniformOrderStat: i = args.uniformOrderStat[0]
      if i < 0 or i >= numPagesToCrop:
         print("\nWarning: The selected order statistic is out of range.",
               "Setting to closest value.", file=sys.stderr)
         if i >= numPagesToCrop: i = numPagesToCrop - 1
         if i < 0: i = 0
      if args.verbose and (args.uniformOrderStat or args.uniformOrderPercent): 
         print("\nThe " + str(i) + 
               " smallest delta values over the selected pages will be ignored"
               "\nwhen choosing a common, uniform delta value for each margin.")
      leftVals  = sorted([ box[0] for box in cropDeltaList ])
      lowerVals = sorted([ box[1] for box in cropDeltaList ])
      rightVals = sorted([ box[2] for box in cropDeltaList ])
      upperVals = sorted([ box[3] for box in cropDeltaList ])
      deltaList = [ [leftVals[i], lowerVals[i], rightVals[i], upperVals[i]] ] * numPages

   # Apply the delta modifications to the full boxes to get the final sizes.
   finalCropList = []
   for fBox, deltas in zip(fullPageBoxList, deltaList):
      finalCropList.append( (fBox[0] + deltas[0], fBox[1] + deltas[1],
                             fBox[2] - deltas[2], fBox[3] - deltas[3]) )

   return finalCropList


def setCroppedMetadata(inputDoc, outputDoc):
   """Set the metadata for the output document.  Mostly just copied over, but
   "Producer" has a string appended to indicate that this program modified the
   file.  That allows for the undo operation to make sure that this
   program cropped the file in the first place."""

   # Check Producer metadata attribute to see if this program cropped document before.
   global producerModifier
   alreadyCroppedByThisProgram = False
   oldProducerString = inputDoc.getDocumentInfo().producer
   if oldProducerString.endswith(producerModifier):
      if args.verbose: 
         print("\nThe document was already cropped at least once by this program.")
      alreadyCroppedByThisProgram = True
      producerModifier = "" # No need to pile up suffixes each time on Producer.
   
   # Setting metadata with pyPdf requires low-level pyPdf operations, see
   # http://stackoverflow.com/questions/2574676/change-metadata-of-pdf-file-with-pypdf
   inputInfoDict = inputDoc.getDocumentInfo()
   outputInfoDict = outputDoc._info.getObject()

   # Note that all None metadata attributes are currently set to the empty string
   # when passing along the metadata information.
   def st(item):
      if item == None: return ""
      else: return item

   outputInfoDict.update({
      NameObject('/Author'): createStringObject(st(inputInfoDict.author)),
      NameObject('/Creator'): createStringObject(st(inputInfoDict.creator)),
      NameObject('/Producer'): createStringObject(st(inputInfoDict.producer)
                                     + producerModifier),
      NameObject('/Subject'): createStringObject(st(inputInfoDict.subject)),
      NameObject('/Title'): createStringObject(st(inputInfoDict.title))
      })

   return alreadyCroppedByThisProgram


def applyCropList(cropList, inputDoc, pageNumsToCrop):
   """Apply the crop list to the pages of the input PdfFileReader object, and
   return a PdfFileWriter object for the cropped version of the document."""

   # Generate the shell of the output document.
   outputDoc = PdfFileWriter()

   # Copy the metadata from inputDot to outputDoc, modifying Producer string.
   alreadyCroppedByThisProgram = setCroppedMetadata(inputDoc, outputDoc)

   if args.restore and not alreadyCroppedByThisProgram:
      print("\nThe Producer string indicates that either this document was not")
      print("previously cropped by pdfCropMargins or else it was modified by")
      print("another program after that.  Trying the undo anyway...")

   if args.verbose and not args.restore:
      print("\nNew full page sizes after cropping, in PDF format (lbrt):")

   # Copy over each page, after modifying the appropriate PDF boxes.
   for page in range(inputDoc.getNumPages()):

      currPage = inputDoc.getPage(page)

      # Only do the restore from ArtBox if '--restore' option was selected.
      if args.restore:
         currPage.mediaBox = currPage.artBox
         currPage.cropBox = currPage.artBox
         outputDoc.addPage(currPage)
         continue

      # Do the save to ArtBox if that option is chosen and Producer is set.
      if not args.noundosave and not alreadyCroppedByThisProgram:
         currPage.artBox = intersectBoxes(currPage.mediaBox, currPage.cropBox)

      # Reset the CropBox and MediaBox to their saved original values
      # (which were set in getFullPageBox, in the currPage object's namespace).
      currPage.mediaBox = currPage.originalMediaBox
      currPage.cropBox = currPage.originalCropBox

      # Copy the original page without further mods if it wasn't in the range
      # selected for cropping.
      if page not in pageNumsToCrop:
         outputDoc.addPage(currPage)
         continue

      # Convert the computed "box to crop to" into a RectangleObject (for pyPdf).
      newCroppedBox = RectangleObject(cropList[page])

      if args.verbose:
         print("   "+str(page+1)+"\t", newCroppedBox) # page num numbering from 1

      if not args.boxesToSet: args.boxesToSet = ["m", "c"]

      # Now set any boxes which were selected to be set via the --boxesToSet option.
      if "m" in args.boxesToSet: currPage.mediaBox = newCroppedBox
      if "c" in args.boxesToSet: currPage.cropBox = newCroppedBox
      if "t" in args.boxesToSet: currPage.trimBox = newCroppedBox
      if "a" in args.boxesToSet: currPage.artBox = newCroppedBox
      if "b" in args.boxesToSet: currPage.bleedBox = newCroppedBox

      outputDoc.addPage(currPage)

   return outputDoc  

##############################################################################
#
# Begin the main script.  
#
##############################################################################

# TODO have routine to clean all filenames and args passed in to external
# commands, in case offered as a service we don't want attacks on system
# by untrusted users ----> start with a do-nothing routine, good enough
# --> or ignore for now, not that important.

def main():

   #
   # Parse and process the command-line arguments.
   #

   global args
   args = parseCommandLineArguments(cmdParser)

   if args.verbose: print("\nProcessing the PDF with pdfCropMargins...\n")

   if args.gsBbox and len(args.fullPageBox) > 1: 
      print("Warning: only one --fullPageBox value can be used with the -gs option.",
            "Ignoring all but the first one.", file=sys.stderr)
      args.fullPageBox = [args.fullPageBox[0]]
   # what if document has no cropbox?  will below fail for gs method or not??? TODO test
   elif args.gsBbox and not args.fullPageBox: args.fullPageBox = ["c"] # gs default
   elif not args.fullPageBox: args.fullPageBox = ["m", "c"] # usual default

   if args.verbose: 
      print("For the full page size, using values from the PDF box"
            "\nspecified by the intersection of these boxes:", args.fullPageBox, "\n")

   if args.absoluteOffset: args.absoluteOffset *= 4 # expand to 4 offsets
   # See if all four offsets are explicitly set and use those if so.
   if args.absoluteOffset4: args.absoluteOffset = args.absoluteOffset4 
   if args.verbose:
      print("The absolute offsets to be added to each margin, in units of bp,"
            " are:\n   ", args.absoluteOffset)

   if args.percentRetain: args.percentRetain *= 4 # expand to 4 percents
   # See if all four percents are explicitly set and use those if so.
   if args.percentRetain4: args.percentRetain = args.percentRetain4
   if args.verbose:
      print("The percentages of margins to retain are:\n   ",
            args.percentRetain)

   inputDocFname = args.pdf_input_doc
   if args.verbose: 
      print("The input document's filename is:\n   ", inputDocFname)
   if not os.path.exists(inputDocFname):
      print("Error in pdfCropMargins: The specified input file\n   "
            + inputDocFname + "\ndoes not exist.", file=sys.stderr)
      sys.exit(1)
   
   if not args.outfile:
      if args.verbose: print("Using the default-generated output filename.")
      outputDocFname = generateDefaultFilename(inputDocFname)
   else:
      outputDocFname = args.outfile[0]
   if args.verbose: 
      print("The output document's filename will be:\n   ", outputDocFname)

   if os.path.exists(outputDocFname) and args.noclobber:
      print("Option '--noclobber' is set, refusing to overwrite existing"
            " output file with filename:\n   ", outputDocFname, file=sys.stderr)
      sys.exit(1)

   # TODO: also test pdftoppm when necessary
   if args.gsBbox or args.gsfix: foundGs = ex.testGsExecutable()
   if args.gsBbox and not foundGs:
      print("\nError in pdfCropMargins: The '--gsBbox' option was specified but"
            "\nthe Ghostscript executable could not be located.  Is it in the"
            "\nPATH for command execution?\n", file=sys.stderr)
      sys.exit(1)
   if args.gsfix and not foundGs:
      print("\nError in pdfCropMargins: The '--gsfix' option was specified but"
            "\nthe Ghostscript executable could not be located.  Is is in the"
            "\nPATH for command execution?\n", file=sys.stderr)
      sys.exit(1)

   #
   # Open in the input document in a PdfFileReader object.
   #

   if args.gsfix:
      if args.verbose:
         print("\nAttempting to fix the PDF input file before reading it...\n")
      tempFileName = ex.fixPdfWithGhostscriptToTmpFile(inputDocFname)
      inputDoc = PdfFileReader(open(tempFileName, "rb"))
      os.remove(tempFileName)
   else:
      inputDoc = PdfFileReader(open(inputDocFname, "rb"))
   
   if args.verbose:
      print("\nThe document's metadata, if set:\n")
      print("   The Author attribute set in the input document is:\n      %s" 
            % (inputDoc.getDocumentInfo().author))
      print("   The Creator attribute set in the input document is:\n      %s" 
            % (inputDoc.getDocumentInfo().creator))
      print("   The Producer attribute set in the input document is:\n      %s" 
            % (inputDoc.getDocumentInfo().producer))
      print("   The Subject attribute set in the input document is:\n      %s" 
            % (inputDoc.getDocumentInfo().subject))
      print("   The Title attribute set in the input document is:\n      %s" 
            % (inputDoc.getDocumentInfo().title))

      print("\nThe input document has %s pages." % inputDoc.getNumPages())
      print()
   
   # Now compute the set containing the pyPdf page number of all the pages
   # which the user has selected for cropping from the command line.  Most
   # calculations are still carried-out for all the pages in the document.
   # (There are a few optimizations for expensive operations like finding
   # bounding boxes; the rest is negligible).  This keeps the correspondence
   # between page numbers and the positions of boxes in the lists.  The
   # function applyCropList then just ignores the cropping information for any
   # pages which were not selected.

   allPageNums = set(range(0,inputDoc.getNumPages()))
   pageNumsToCrop = set()
   if args.pages:
      for pageNumOrRange in args.pages.split(","):
         splitRange = pageNumOrRange.split("-")
         try:
            if len(splitRange) == 1: 
               # Note pyPdf page nums start at 0, not 1 like usual PDF pages, subtract 1.
               pageNumsToCrop.add(int(splitRange[0])-1)
            else:
               pageNumsToCrop.update(set(range(int(splitRange[0])-1, int(splitRange[1]))))
         except ValueError:
            print("\nError in pdfCropMargins: The page range specified on the command line",
                  "\ncontains a non-integer value or otherwise cannot be parsed.",
                  file=sys.stderr)
            sys.exit(1)
      pageNumsToCrop = pageNumsToCrop & allPageNums # intersect chosen with actual
   else:
      pageNumsToCrop = allPageNums

   # Print out the pages to crop in verbose mode.
   if args.verbose and args.pages:
      print("These pages of the document will be cropped:", end="")
      pNumList = sorted(list(pageNumsToCrop))
      for i in range(len(pNumList)):
         if i % 10 == 0 and i != len(pNumList)-1: print("\n   ", end="")
         print("%5d" % (pNumList[i]+1), " ", end="")
      print("\n")
   elif args.verbose:
      print("All the pages of the document will be cropped.\n")

   #
   # Get a list with the full-page boxes for each page: (left,bottom,right,top)
   # This also sets the MediaBox and CropBox of the pages to the chosen full-page
   # size.
   #

   fullPageBoxList = getFullPageBoxList(inputDoc)

   #
   # Calculate the boundingBoxList containing tight page bounds.
   #

   if not args.restore:
      boundingBoxList = getBoundingBoxList(inputDocFname, inputDoc, fullPageBoxList,
                                                pageNumsToCrop, args, PdfFileWriter)

   #
   # Calculate the cropList based on the bounding box and the fullpage box.
   #

   if not args.restore:
      cropList = calculateCropList(fullPageBoxList, boundingBoxList, pageNumsToCrop)
   else:
      cropList = None

   #
   # Apply the calculated crops, setting outputDoc to the returned PdfFileWriter.
   #

   outputDoc = applyCropList(cropList, inputDoc, pageNumsToCrop)

   #
   # Write the final PDF out to a file.
   #

   if args.verbose: print("\nWriting the cropped PDF file.")
   outputDocStream = open(outputDocFname, "wb")
   outputDoc.write(outputDocStream)
   outputDocStream.close()

   # 
   # Now handle the options which occur after the file is written.
   #

   if args.preview: 
      viewer = args.preview
      if args.verbose:
         print("Previewing the output document with viewer:\n   ", viewer)   
      os.system(viewer + " " + outputDocFname) # system call will wait completion

   # Handle the '--queryModifyOriginal' option.
   if args.queryModifyOriginal:
      getInput = input # Python3 raw input reading
      try: # for Python2 compatibility
         getInput = raw_input # Python2 raw input reading
      except NameError:
         pass
      while True:
         queryString = "Modify the original file? [yn] "
         queryResult = getInput(queryString).decode("utf-8")
         if queryResult in ["y","Y"]:
            args.modifyOriginal = True
            print("Modifying the original file.")
            break
         elif queryResult in ["n","N"]:
            print("Not modifying the original file.")
            args.modifyOriginal = False
            break
         else:
            print("Response must be in the set {y,Y,n,N}, none recognized.")
            continue

   # Handle the '--modifyOriginal' option.
   if args.modifyOriginal:
      origArchivedName = generateDefaultFilename(inputDocFname, croppedFile=False)
      
      # Remove any existing file with the name origArchivedName unless a
      # relevant noclobber option is set.
      if os.path.exists(origArchivedName):
         if not args.noclobberOriginal and not args.noclobber:
            if args.verbose: print("Removing the file", origArchivedName)
            os.remove(origArchivedName)
         else:
            print("A noclobber option is set; refusing to overwrite file:\n   ",
                  origArchivedName, 
                  "\nFiles are as if option '--modifyOriginal' were not set.",
                  file=sys.stderr)
      
      # Move (noclob) the original file to the name for uncropped files.
      if not os.path.exists(origArchivedName):
         if args.verbose: print("Doing a file move:", inputDocFname, origArchivedName)
         shutil.move(inputDocFname, origArchivedName)

      # Move (noclob) the cropped file to the original file's name.
      if not os.path.exists(inputDocFname):
         if args.verbose: print("Doing a file move:", outputDocFname, inputDocFname)
         shutil.move(outputDocFname, inputDocFname)


   if args.verbose: print("\nFinished this run of pdfCropMargins.\n")
   

if __name__ == "__main__":
   main()

